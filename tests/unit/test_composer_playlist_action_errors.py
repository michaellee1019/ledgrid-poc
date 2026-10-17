"""Playlist errors survive polling and uncertain starts reuse one request."""
from pathlib import Path
import subprocess


def test_playlist_failure_recovery_and_cancelled_start():
    script = Path('web/static/js/composer_slice.js').read_text()
    source = script[script.index('  function renderPlaylistActionFailure()'):script.index('  function wireLiveRange(')]
    runner = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
function environment(){
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',dataset:{}});return nodes.get(id);};
 const state={playlist:{requestId:null,runId:null,startIntent:null,statusIntent:0,statusPoll:0}};
 const starts=[],stops=[];let stopGate=null,saves=0,fail=true,status={request:null,current:{phase:'stopped',run_id:'old-run'}};
 const context={state,$,AbortSignal,api:'/api',newUuid:()=>`request-${starts.length+1}`,renderPlaylist(){context.renderError();},savePlaylist:async()=>{saves++;return{id:'saved-playlist'};},
  requestJson:async(url,options)=>{
   if(url.endsWith('/run')){assert.ok(options.signal);const id=options.headers['Idempotency-Key'];starts.push(id);if(fail)throw Object.assign(new Error('Lost start response'),{code:'offline'});return{accepted:{request_id:id,run_id:'new-run'}};}
   if(url.endsWith('/stop')){stops.push(JSON.parse(options.body));if(stopGate)await stopGate;return{accepted:{request_id:'stop-request',run_id:'new-run'}};}
   return status;
  }};
 vm.runInNewContext(process.argv[1]+';this.start=startPlaylist;this.stop=stopPlaylist;this.refresh=refreshPlaylistStatus;this.renderError=renderPlaylistActionFailure;this.abandon=abandonPlaylistStart;',context);
 return{context,state,$,starts,stops,saves:()=>saves,success:()=>fail=false,status:value=>status=value,holdStop:()=>{let release;stopGate=new Promise(resolve=>release=resolve);return release;}};
}
(async()=>{
 const env=environment();await env.context.start();
 assert.equal(env.state.playlist.retryStart.requestId,env.starts[0]);
 const error=env.$('#playlistActionMessage').textContent;
 await env.context.refresh();await env.context.refresh();
 assert.equal(env.$('#playlistActionMessage').textContent,error,'old stopped polls cannot hide action failure');
 assert.equal(env.starts.length,1,'status recovery sends no playback command');
 await env.context.start();assert.equal(env.starts.length,1,'new start blocked until uncertain request settles');
 env.success();const retry=env.state.playlist.retryStart;await Promise.all([env.context.start(retry),env.context.start(retry)]);
 assert.equal(env.starts.length,2);assert.equal(env.starts[0],env.starts[1],'retry uses original idempotency key');assert.equal(env.saves(),1,'retry cannot overwrite saved request snapshot');
 assert.equal(env.state.playlist.actionError,null);assert.equal(env.state.playlist.retryStart,null);
 env.state.playlist.runId='new-run';env.state.playlist.actionError={kind:'stop',message:'Failed stop'};
 const release=env.holdStop(),first=env.context.stop(),second=env.context.stop();
 assert.equal(env.stops.length,1,'pending Stop retries coalesce');
 assert.equal(env.$('#playlistRetryAction').disabled,true);assert.equal(env.$('#playlistRefreshStatus').disabled,true);
 release();await Promise.all([first,second]);assert.equal(env.$('#playlistStop').disabled,false);
 const abandoned=environment();await abandoned.context.start();await abandoned.context.stop();await abandoned.context.refresh();
 assert.equal(abandoned.$('#playlistUnlockEditing').hidden,false,'uncertain cancelled start has explicit manual recovery');
 abandoned.context.abandon();assert.equal(abandoned.state.playlist.retryStart,null,'manual recovery unlocks editing');
 assert.match(abandoned.state.playlist.actionError.message,/may still run/);assert.equal(abandoned.starts.length,1,'manual recovery cannot replay start');

 const cancelled=environment();await cancelled.context.start();await cancelled.context.stop();
 assert.equal(cancelled.state.playlist.retryStart.cancelled,true);
 await cancelled.context.start(cancelled.state.playlist.retryStart);assert.equal(cancelled.starts.length,1,'a cancelled uncertain start is never replayed');
 cancelled.status({request:{request_id:cancelled.starts[0],run_id:'new-run',phase:'running'},current:{run_id:'new-run',phase:'running'}});
 await cancelled.context.refresh();await cancelled.context.refresh();
 assert.deepEqual(cancelled.stops,[{run_id:'new-run'}],'Stop follows the recovered original run once');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', runner, source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
