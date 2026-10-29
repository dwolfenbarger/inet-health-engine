import {useEffect,useState} from 'react'

export function useEvidence<T>(url:string,enabled=true){
 const [revision,setRevision]=useState(0),[state,setState]=useState<{key:string;data:T|null;error:string}>({key:'',data:null,error:''})
 const key=url+'|'+revision
 useEffect(()=>{if(!enabled)return;let stopped=false;const controller=new AbortController()
  void(async()=>{try{const r=await fetch(url,{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(24000)])});if(!r.ok){let message='Evidence unavailable; no health conclusion is available.';try{const body=await r.json() as {detail?:unknown};if(typeof body.detail==='string')message=body.detail}catch{/* Keep transport details private. */}throw new Error(message)}const data=await r.json() as T;if(!stopped)setState({key,data,error:''})}catch(e){if(!stopped)setState({key,data:null,error:e instanceof Error?e.message:'Evidence unavailable'})}})()
  return()=>{stopped=true;controller.abort()}
 },[url,key,enabled])
 return {data:enabled&&state.key===key?state.data:null,error:enabled&&state.key===key?state.error:'',loading:enabled&&state.key!==key,refresh:()=>setRevision(r=>r+1)}
}
