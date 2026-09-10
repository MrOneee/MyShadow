/** DSH codeRuntime backed by QuickJS/WASM, with no Node, filesystem or network globals. */
import {getQuickJS} from 'quickjs-emscripten';
import {stripTypeScriptTypes} from 'node:module';

export async function runProgram(request, limits={}) {
  const wallMs=limits.wallMs??120000, computeMs=limits.computeMs??2000;
  const outputBytes=limits.outputBytes??24000;
  const quick=await getQuickJS(), runtime=quick.newRuntime();
  runtime.setMemoryLimit(limits.memoryBytes??32*1024*1024);
  runtime.setMaxStackSize(512*1024);
  const vm=runtime.newContext(), logs=[], deferred=[];
  let closed=false, used=0, sliceStart=0, outputSize=0;
  const started=performance.now();
  const stopped=()=>request.signal?.aborted || performance.now()-started>wallMs || used+(sliceStart?performance.now()-sliceStart:0)>computeMs;
  runtime.setInterruptHandler(stopped);
  const execute=fn=>{sliceStart=performance.now();try{return fn();}finally{used+=performance.now()-sliceStart;sliceStart=0;}};
  const evaluate=code=>execute(()=>vm.unwrapResult(vm.evalCode(code)));
  const fail=(kind,message)=>({logs,error:{kind,message:String(message).slice(0,1000)}});
  let main;
  try {
    if(request.signal?.aborted)return fail('abort','Cancelled');
    if(request.program.length>40000)return fail('output-limit','Program too large');
    vm.newFunction('__dispatch',(namespace,member,input)=>{
      const ns=vm.getString(namespace),name=vm.getString(member),args=JSON.parse(vm.getString(input));
      const binding=request.bindings.find(b=>b.global===ns);
      if(!binding || !Object.hasOwn(binding.functions,name))throw new Error('Tool unavailable');
      const promise=vm.newPromise();deferred.push(promise);
      // Only the DSH-supplied binding crosses the boundary; the guest never gets a host object.
      Promise.resolve().then(()=>{if(closed||stopped())throw new Error('Cancelled');return binding.functions[name](args);}).then(
        value=>{if(!closed){const h=vm.newString(JSON.stringify(value));promise.resolve(h);h.dispose();}},
        error=>{if(!closed){const h=vm.newString(String(error.message??error));promise.reject(h);h.dispose();}}
      ).catch(()=>{});
      return promise.handle;
    }).consume(h=>vm.setProp(vm.global,'__dispatch',h));
    vm.newFunction('__log',input=>{
      const value=vm.getString(input);outputSize+=Buffer.byteLength(value);
      if(outputSize>outputBytes)throw new Error('Output limit exceeded');
      logs.push(value);
    }).consume(h=>vm.setProp(vm.global,'__log',h));
    let bootstrap='const console=Object.freeze({log:(...a)=>__log(a.map(x=>typeof x==="string"?x:JSON.stringify(x)).join(" "))});\n';
    for(const binding of request.bindings){
      const ns=JSON.stringify(binding.global),error=binding.errorClass;
      if(error)bootstrap+=`globalThis[${JSON.stringify(error.name)}]=class extends Error {constructor(message,name){super(message);this.name=${JSON.stringify(error.name)};this[${JSON.stringify(error.memberNameProperty)}]=name;}};\n`;
      bootstrap+=`globalThis[${ns}]=Object.create(null);\n`;
      for(const name of Object.keys(binding.functions)){
        const key=JSON.stringify(name);
        bootstrap+=`globalThis[${ns}][${key}]=async(args)=>{try{return JSON.parse(await __dispatch(${ns},${key},JSON.stringify(args)));}catch(e){throw ${error?`new globalThis[${JSON.stringify(error.name)}](String(e),${key})`:'new Error(String(e))'};}};\n`;
      }
      bootstrap+=`Object.freeze(globalThis[${ns}]);\n`;
    }
    evaluate(bootstrap).dispose();
    const program=stripTypeScriptTypes(`(async()=>{${request.program}\n})()`,{mode:'strip'});
    main=evaluate(program);
    let settled;
    vm.resolvePromise(main).then(result=>{settled=result;});
    while(!settled){
      if(stopped())return fail(request.signal?.aborted?'abort':'timeout','PTC execution budget exceeded');
      const jobs=execute(()=>runtime.executePendingJobs());
      if(jobs.error){const message=vm.dump(jobs.error);jobs.error.dispose();return fail('exception',JSON.stringify(message));}
      await new Promise(resolve=>setTimeout(resolve,5));
    }
    if(settled.error){const message=vm.dump(settled.error);settled.error.dispose();return fail(stopped()?'timeout':'exception',JSON.stringify(message));}
    const value=vm.dump(settled.value);settled.value.dispose();
    if(Buffer.byteLength(JSON.stringify({logs,value})??'')>outputBytes)return fail('output-limit','Output limit exceeded');
    return value===undefined?{logs}:{logs,value};
  } catch(error){return fail(stopped()?(request.signal?.aborted?'abort':'timeout'):'exception',error.message??error);}
  finally {
    closed=true;
    main?.dispose();
    for(const promise of deferred)promise.dispose();
    vm.dispose();runtime.dispose();
  }
}

export const name='wechat-ptc-runtime';
export function apply(ctx){ctx.provide('codeRuntime',{language:'typescript',isolation:'quickjs-wasm',run:runProgram});}
