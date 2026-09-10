import test from 'node:test';
import assert from 'node:assert/strict';
import {runProgram} from '../agent_runtime/ptc_runtime.mjs';
const run=(program,bindings=[],limits={})=>runProgram({program,bindings},limits);
test('typed async composition returns only selected output',async()=>{
  const result=await run('const r: number[]=await tools.rows({});console.log("ok");return r.reduce((a,b)=>a+b,0)',
    [{global:'tools',functions:{rows:async()=>[2,3,4]}}]);
  assert.deepEqual(result,{logs:['ok'],value:9});
});
test('no ambient host access or cross-run state',async()=>{
  assert.deepEqual((await run('return [typeof process,typeof require,typeof fetch,typeof Buffer]')).value,Array(4).fill('undefined'));
  assert.ok((await run('return await import("node:fs")')).error);
  assert.equal((await run('globalThis.secret=42;return 1')).value,1);
  assert.equal((await run('return typeof secret')).value,'undefined');
  assert.equal((await run('return console.log.constructor("return typeof process")()')).value,'undefined');
});
test('binding failures remain catchable tool errors',async()=>{
  const result=await run('try{await tools.bad({})}catch(e){return [e.name,e.toolName,e.message]}',
    [{global:'tools',errorClass:{name:'ToolCallError',memberNameProperty:'toolName'},functions:{bad:async()=>{throw new Error('failed')}}}]);
  assert.deepEqual(result.value,['ToolCallError','bad','failed']);
});
test('infinite code and unresolved promises have bounded lifetimes',async()=>{
  assert.equal((await run('while(true){}',[],{computeMs:30})).error.kind,'timeout');
  assert.equal((await run('await new Promise(()=>{})',[],{wallMs:30})).error.kind,'timeout');
});
test('output limit and cancellation',async()=>{
  assert.equal((await run('return "x".repeat(50000)')).error.kind,'output-limit');
  const abort=new AbortController();abort.abort();
  assert.equal((await runProgram({program:'return 1',bindings:[],signal:abort.signal})).error.kind,'abort');
});
test('unawaited bindings cannot access disposed VM',async()=>{
  const result=await run('tools.slow({});return 1',[{global:'tools',functions:{slow:()=>new Promise(r=>setTimeout(()=>r({}),30))}}]);
  assert.equal(result.value,1);
  await new Promise(r=>setTimeout(r,50));
});
