"""Development-only, bounded lossless archive profiling. No expiry or public API."""
import argparse,gzip,hashlib,json,os,re,tempfile
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

MAX_ROW=8*1024**2

def encoded(record):
    return (json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False,default=str)+'\n').encode()

def atomic_json(path,value):
    path=Path(path)
    data=encoded(value)
    fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class JournalCoverage:
    def __init__(self,expected):
        self.expected=expected;self.counts=Counter();self.seen={}
        for observer,row in expected.items():
            limit=int(row['sequence'])
            if limit<0 or limit>50_000_000:raise ValueError('Sequence budget exceeded')
            if int(row['messages'])!=limit:raise ValueError('Checkpoint/counter differs; cannot qualify full journal')
            self.seen[observer]=bytearray((limit+7)//8)
    def add(self,row):
        observer=row['observer'];seq=int(row['sequence'])
        if observer not in self.expected or not 1<=seq<=int(self.expected[observer]['sequence']):
            raise ValueError('Journal sequence outside snapshot checkpoint')
        index,bit=divmod(seq-1,8);mask=1<<bit
        if self.seen[observer][index]&mask:raise ValueError('Duplicate journal sequence')
        self.seen[observer][index]|=mask;self.counts[observer]+=1
    def finish(self):
        for observer,row in self.expected.items():
            if self.counts[observer]!=int(row['messages']):raise ValueError('Journal coverage is incomplete: '+observer)
        return dict(self.counts)

class Bundle:
    def __init__(self,root,*,environment='development',max_raw=4*1024**3,max_stored=1024**3,part_raw=32*1024**2):
        if environment!='development':raise ValueError('Full archive profiles are development-only')
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=False)
        self.max_raw=max_raw;self.max_stored=max_stored;self.part_raw=part_raw
        self.raw=0;self.stored=0;self.objects=[];self.writer=None
    def _open(self,dataset):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,60}',dataset):raise ValueError('Unsafe dataset name')
        self.dataset=dataset;self.rows=0;self.part_size=0;self.digest=hashlib.sha256()
        fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=self.root);self.tmp=Path(tmp)
        self.file=os.fdopen(fd,'wb');self.writer=gzip.GzipFile(fileobj=self.file,mode='wb',filename='',mtime=0)
    def add(self,dataset,row):
        data=encoded(row)
        if len(data)>MAX_ROW:raise ValueError('Record exceeds input bound')
        if self.raw+len(data)>self.max_raw:raise ValueError('Raw profile budget exceeded')
        if self.writer and (dataset!=self.dataset or self.part_size+len(data)>self.part_raw):self.close_part()
        if self.writer is None:self._open(dataset)
        self.writer.write(data);self.digest.update(data);self.rows+=1;self.part_size+=len(data);self.raw+=len(data)
        if self.stored+self.file.tell()>self.max_stored:raise ValueError('Compressed profile budget exceeded')
    def close_part(self):
        if self.writer is None:return
        self.writer.close();self.file.flush();os.fsync(self.file.fileno());self.file.close();self.writer=None
        size=self.tmp.stat().st_size
        if self.stored+size>self.max_stored:raise ValueError('Compressed profile budget exceeded')
        stored_hash=hashlib.sha256(self.tmp.read_bytes()).hexdigest()
        name=self.dataset+'-'+stored_hash+'.jsonl.gz';destination=self.root/name
        if destination.exists():raise ValueError('Duplicate immutable object name')
        self.tmp.rename(destination);self.stored+=size
        self.objects.append({'file':name,'dataset':self.dataset,'rows':self.rows,'raw_bytes':self.part_size,
            'stored_bytes':size,'sha256':stored_hash,'decoded_sha256':self.digest.hexdigest()})
        print('PROFILE_PART',self.dataset,self.rows,self.part_size,size,flush=True)
    def abort(self):
        if self.writer:
            self.writer.close();self.file.close();self.writer=None
        for pending in self.root.glob('.pending-*'):pending.unlink()
    def finish(self,metadata):
        self.close_part()
        manifest={**metadata,'schema':'inet-retention-profile-v1','environment':'development',
            'complete':True,'expiry_eligible':False,'created_at':datetime.now(timezone.utc).isoformat(),
            'raw_bytes':self.raw,'stored_bytes':self.stored,'objects':self.objects}
        atomic_json(self.root/'manifest.json',manifest)
        return manifest

def verify(root,*,max_raw=4*1024**3,max_stored=1024**3):
    root=Path(root).resolve();manifest_path=root/'manifest.json'
    if manifest_path.is_symlink():raise ValueError('Symlink manifest refused')
    if manifest_path.stat().st_size>2*1024**2:raise ValueError('Manifest size bound')
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('schema')!='inet-retention-profile-v1' or manifest.get('environment')!='development' or manifest.get('complete') is not True:
        raise ValueError('Unqualified development manifest')
    if manifest.get('expiry_eligible') is not False:raise ValueError('Pilot cannot qualify deletion')
    coverage=JournalCoverage(manifest['journal_expected']) if manifest.get('journal_expected') is not None else None
    raw=stored=rows=0;seen=set();datasets=Counter()
    for item in manifest['objects']:
        name=item['file']
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,60}-[a-f0-9]{64}\.jsonl\.gz',name) or name in seen:raise ValueError('Unsafe/duplicate object reference')
        seen.add(name);path=root/name
        if path.is_symlink() or path.resolve().parent!=root:raise ValueError('Object escapes archive root')
        size=path.stat().st_size;stored+=size
        if stored>max_stored or size!=item['stored_bytes']:raise ValueError('Stored size mismatch/bound')
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
        if digest.hexdigest()!=item['sha256']:raise ValueError('Stored checksum mismatch')
        count=length=0;decoded=hashlib.sha256()
        with gzip.open(path,'rb') as source:
            while True:
                line=source.readline(MAX_ROW+1)
                if not line:break
                if len(line)>MAX_ROW:raise ValueError('Decoded record bound')
                raw+=len(line);length+=len(line)
                if raw>max_raw:raise ValueError('Decoded archive budget exceeded')
                row=json.loads(line);decoded.update(line);count+=1
                if coverage and item['dataset']=='routing_messages':coverage.add(row)
        if count!=item['rows'] or length!=item['raw_bytes'] or decoded.hexdigest()!=item['decoded_sha256']:
            raise ValueError('Decoded count/size/checksum mismatch')
        rows+=count;datasets[item['dataset']]+=count
    if raw!=manifest['raw_bytes'] or stored!=manifest['stored_bytes']:raise ValueError('Manifest totals mismatch')
    journal=coverage.finish() if coverage else None
    return {'verified':True,'development_only':True,'expiry_eligible':False,'records':rows,'datasets':dict(datasets),
        'journal_counts':journal,'raw_bytes':raw,'stored_bytes':stored,'ratio':round(raw/stored,3) if stored else None}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',required=True)
    print(json.dumps(verify(parser.parse_args().verify),sort_keys=True))
