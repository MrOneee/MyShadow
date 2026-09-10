/** Plugin API pinned to DSH 0.1.2rc1. Tools and policies are host-owned. */
import {readFileSync} from 'node:fs';
import {unreadablePage} from './web_result.mjs';
export const name='wechat-tools';
export const inject=['tools'];
export function apply(ctx) {
  const config=JSON.parse(readFileSync(process.env.WECHAT_HARNESS_SPEC,'utf8'));
  let steps=0, calls=0;
  const blockedHosts=new Map();
  const post=async(payload,signal)=>{
    const response=await fetch(config.endpoint,{method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+config.token},
      body:JSON.stringify(payload),signal});
    if (!response.ok) throw new Error('Host tool bridge HTTP '+response.status);
    return await response.json();
  };
  ctx.on('agent/pre-step',async (_payload,next)=>{
    if (++steps>config.max_steps) throw new Error('HARNESS_STEP_BUDGET_EXCEEDED');
    return await next();
  });
  ctx.on('tools/pre-execute',async (exec,next)=>{
    // Outer PTC envelopes do not consume the subtool budget or block final submission.
    if (exec.name==='run_code') return await next();
    // Leave the final three model steps for validation/repair, not more browsing.
    if (exec.name==='submit_result') return await next();
    if (exec.name==='web_fetch') {
      const host=new URL(exec.arguments.url).hostname;
      if ((blockedHosts.get(host)||0)>=2) return {kind:'deny',reason:'本轮此网站已两次无法读取正文，请换公开来源，不再重复请求。'};
    }
    if (steps>config.max_steps-3) return {kind:'deny',reason:'本轮已进入收尾阶段。请立即提交实际结果；资料不足按任务定义提交不足原因，不再检索。'};
    if (++calls>config.max_tools) return {kind:'deny',reason:'本次工具预算已用完，请根据已有结果完成任务，不能声称未完成的操作已成功。'};
    return await next();
  });
  ctx.on('tools/post-execute',async (exec,result,next)=>{
    const decision=await next();
    const issue=exec.name==='web_fetch'&&!result.isError ? unreadablePage(result.value) : null;
    if (issue && decision.kind!=='block') {
      const host=new URL(exec.arguments.url).hostname;
      blockedHosts.set(host,(blockedHosts.get(host)||0)+1);
      await post({event:'web_result',name:exec.name,args:exec.arguments,call_id:exec.callId,
        result:{...result,isError:true,error:issue}},exec.signal);
      return {kind:'block',feedback:[{type:'text',text:issue.code+': '+issue.message}]};
    }
    if (exec.name==='web_search'||exec.name==='web_fetch')
      await post({event:'web_result',name:exec.name,args:exec.arguments,call_id:exec.callId,result},exec.signal);
    return decision;
  });
  for (const tool of config.tools) {
    const {output_schema,...definition}=tool;
    ctx.tools.register({...definition,
      output:{schema:output_schema??{},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
      async execute(args,exec) {
        const result=await post({name:tool.name,args,call_id:exec.callId},exec.signal);
        if (result?.harness_conclude===true) exec.concludeTurn();
        return result;
      }
    });
  }
}
