"""Stop waits for its file-controller command rather than a transient idle sample."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from web.app import AnimationWebInterface
from tests.unit.test_composer_slice import _PreviewManager

class AsyncStopTests(unittest.TestCase):
    def test_stop_endpoint_returns_the_queued_command_request_identity(self):
        class Channel:
            def send_command(self,action,**data):
                self.command={'action':action,'data':data}
                return {'request_id':'00000000-0000-4000-8000-000000000001','command_id':123}
        channel=Channel()
        with tempfile.TemporaryDirectory() as d:
            interface=AnimationWebInterface(channel,_PreviewManager(),project_root=Path(d),activation_enabled=True)
            response=interface.app.test_client().delete('/api/v1/scene')
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.get_json()['request_id'],'00000000-0000-4000-8000-000000000001')
        self.assertEqual(channel.command,{'action':'stop_scene','data':{}})
    def test_browser_stop_does_not_finish_on_a_transient_stopped_sample(self):
        source=Path('web/static/js/composer_slice.js').read_text()
        functions=source[source.index('  async function waitForStopCommand'):source.index('  function stopOutput()',source.index('  async function waitForStopCommand'))]
        script=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const calls=[],nodes={'#liveAction':{},'#operationMessage':{}};
const state={status:{},wall:{bootstrap:{capabilities:{server_actions:{activation_available:true}}},observation:{is_running:true}}};
let observations=0,paused,release;
const atFirstObservation=new Promise(resolve=>paused=resolve);
const transient=new Promise(resolve=>release=resolve);
const context={state,Date,AbortController,window:{setTimeout,clearTimeout},encodeURIComponent,api:'/api/composer',clientId:'client',
 $:selector=>nodes[selector],sleep:async()=>{},renderStatus(){},wallStatus:()=>({}),blockers(){},
 requestJson:async(url,options)=>{
  calls.push({url,method:options?.method});
  if(options?.method==='DELETE')return {request_id:'stop-1'};
  if(url.includes('/settings/observed')){
   observations+=1;if(observations===1){paused();await transient;return {freshness:'fresh',is_running:false,command_result:{request_id:'prior-edit',state:'completed'}};}
   return {freshness:'fresh',is_running:false,command_result:{request_id:'stop-1',state:'completed'}};
  }
  return {status:{running:false}};
 }};
vm.runInNewContext(process.argv[1]+';this.stopNow=stopOutputNow;',context);
(async()=>{
 const stopping=context.stopNow({});await atFirstObservation;
 assert.equal(calls[0].method,'DELETE','Stop must be sent before any status read');
 assert.equal(state.wall.activating,true,'a transient idle state must keep Stop pending');
 assert.equal(calls.some(call=>call.url==='/api/composer/stop'),false);
 release();await stopping;
 assert.equal(observations,2,'only the Stop request completion settles the action');
 assert.equal(state.wall.activating,false);assert.equal(state.wall.observation.is_running,false);
 assert.equal(calls.at(-1).url,'/api/composer/stop');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script,functions],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_entire_stop_operation_aborts_stalled_network_requests(self):
        source=Path('web/static/js/composer_slice.js').read_text()
        request_function=source[source.index('  async function requestJson('):source.index('  const number',source.index('  async function requestJson('))]
        stop_functions=source[source.index('  async function waitForStopCommand'):source.index('  function stopOutput()',source.index('  async function waitForStopCommand'))]
        script=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
(async()=>{
 for(const stalledAt of ['delete','observation','local-stop']){
  const requests=[],nodes={'#liveAction':{},'#operationMessage':{}},alerts=[];let aborted=0,cleared=0;
  const state={status:{},wall:{bootstrap:{capabilities:{server_actions:{activation_available:true}}},observation:{is_running:true}}};
  const context={state,Date,AbortController,encodeURIComponent,api:'/api/composer',clientId:'client',
   window:{setTimeout:(callback,milliseconds)=>{assert.equal(milliseconds,10000);return setTimeout(callback,20)},clearTimeout:timer=>{cleared+=1;clearTimeout(timer)}},
   $:selector=>nodes[selector],sleep:async()=>{},renderStatus(){},wallStatus:()=>({}),blockers:alert=>alerts.push(alert),
   fetch:(url,options)=>{
    const phase=options.method==='DELETE'?'delete':url.includes('/settings/observed')?'observation':'local-stop';
    assert.ok(options.signal,'every Stop fetch must share the deadline signal');
    if(requests.length)assert.equal(options.signal,requests[0].signal);
    requests.push({url,signal:options.signal,phase});
    if(phase===stalledAt){
     assert.equal(state.wall.activating,true);
     // This transport deliberately never settles unless the fetch is aborted.
     return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{aborted+=1;reject(Object.assign(new Error('aborted'),{name:'AbortError'}));},{once:true}));
    }
    const body=phase==='delete'?{request_id:'stop-1'}:phase==='observation'?{freshness:'fresh',is_running:false,command_result:{request_id:'stop-1',state:'completed'}}:{status:{running:false}};
    return Promise.resolve({ok:true,json:async()=>body});
   }};
  vm.runInNewContext(process.argv[1]+process.argv[2]+';this.stopNow=stopOutputNow;',context);
  const started=Date.now();await context.stopNow({});
  assert.ok(Date.now()-started<500,`stalled ${stalledAt} must terminate on the shared deadline`);
  assert.equal(aborted,1);assert.equal(cleared,1);assert.equal(state.wall.activating,false);
  assert.equal(state.wall.activationError,'Stop timed out; refresh to check output.');
  assert.equal(alerts[0].error,state.wall.activationError);
  assert.equal(requests.at(-1).phase,stalledAt,'timeout must not issue follow-on requests');
 }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script,request_function,stop_functions],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
