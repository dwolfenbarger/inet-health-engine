import {test} from 'node:test'
import assert from 'node:assert/strict'
import {initialState, parseInvestigation, serializeInvestigation, visibleIncidents, sourceState, comparisonPaths} from '../src/preview/investigation.ts'
test('all investigation context survives a URL round trip',()=>{
  const state={...initialState,workspace:'compare',mode:'3d',asn:64498,prefix:'2001:db8::/48',window:'24h',family:'ipv6',severity:'major',scenario:'partial',observer:'amsterdam',comparison:'singapore'}
  assert.deepEqual(parseInvestigation(serializeInvestigation(state)),state)
})
test('invalid or untrusted URL values fall back to supported fixtures',()=>{
  assert.deepEqual(parseInvestigation('#view=unknown&asn=-1&prefix=<script>&map=bad'),initialState)
})
test('time, family, severity and observer filter evidence',()=>{
  assert.equal(visibleIncidents(initialState).length,2)
  assert.equal(visibleIncidents({...initialState,window:'6h'}).length,3)
  assert.equal(visibleIncidents({...initialState,window:'6h',family:'ipv6'})[0].id,'DEMO-024')
  assert.equal(visibleIncidents({...initialState,severity:'major'}).length,1)
  assert.equal(visibleIncidents({...initialState,observer:'amsterdam'})[0].id,'DEMO-021')
})
test('no observations never become a healthy result',()=>{
  assert.equal(visibleIncidents({...initialState,scenario:'unavailable'}).length,0)
  assert.equal(sourceState('unavailable').score,null)
  assert.match(sourceState('unavailable').detail,/Unknown/)
})
test('demonstration and partial evidence never create a live score',()=>{
  assert.equal(sourceState('demo').score,null)
  assert.equal(sourceState('partial').score,null)
})
test('comparison fixtures keep the requested origin without invented repeated hops',()=>{
  for(const origin of [64496,64497,64498,64499])for(const observer of ['amsterdam','singapore']){
    const {left,right}=comparisonPaths(origin,observer)
    for(const path of [left,right]){assert.equal(path.at(-1),origin);assert.equal(new Set(path).size,path.length)}
  }
})
