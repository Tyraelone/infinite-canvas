import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from '../../web/node_modules/typescript/lib/typescript.js';

function load(path, require = () => { throw new Error('Unexpected runtime dependency'); }, globals = {}) {
    const exports = {};
    const scope = { exports, require, console, URL, decodeURIComponent, ...globals };
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,scope);
    return exports;
}
const utils=load('../../web/src/app/(user)/canvas/utils/canvas-video-composition.ts');
const tools=load('../../web/src/app/(user)/canvas/agent/canvas-agent-tools.ts', name=>name==='nanoid'?{nanoid:()=> 'action-id'}:utils);
const node=(id,key)=>({id,type:'video',title:id,position:{x:0,y:0},width:420,height:240,metadata:{content:'/api/local-runtime/workspace/files/'+encodeURIComponent(key),storageKey:key,status:'success'}});

test('Agent composition retains explicit order and repeated source with different trims',()=>{
    const action=tools.normalizeCanvasAgentAction('compose_videos',{title:'test',clips:[{nodeId:'b',startSeconds:.25,durationSeconds:2},{nodeId:'a',durationSeconds:1},{nodeId:'b',startSeconds:4,durationSeconds:.5}]},'action');
    const request=utils.compositionRequest(action.arguments,[node('a','video:a'),node('b','video:b')],'a'.repeat(32));
    assert.deepEqual(Array.from(request.clips,c=>c.storageKey),['video:b','video:a','video:b']);
    assert.equal(request.clips[2].startSeconds,4);
    assert.equal(request.keepAudio,true);
});
test('invalid times, missing and non-video sources fail before submission',()=>{
    for(const duration of [0,-1,NaN,Infinity,true,'5'])assert.throws(()=>utils.normalizeCompositionOptions({clips:[{nodeId:'a',durationSeconds:duration}]}));
    assert.throws(()=>utils.compositionRequest({clips:[{nodeId:'missing',durationSeconds:1}]},[],'id'),/找不到/);
    assert.throws(()=>utils.compositionRequest({clips:[{nodeId:'a',durationSeconds:1}]},[{...node('a','video:a'),type:'image'}],'id'),/视频/);
});
test('late result cannot replace a retried composition or change source nodes',()=>{
    const source=node('a','video:a');const pending={...node('out',''),metadata:{compositionTaskId:'new',status:'loading'}};
    const old=utils.applyCompositionTaskUpdate([source,pending],'out',{id:'old',status:'completed',url:'bad'});
    assert.equal(old[1],pending);
    const next=utils.applyCompositionTaskUpdate([source,pending],'out',{id:'new',status:'completed',url:'good',storageKey:'video:out',width:864,height:480,durationSeconds:3,bytes:123,progress:100});
    assert.equal(next[0],source);assert.equal(next[1].metadata.content,'good');assert.equal(next[1].metadata.status,'success');assert.equal(next[1].metadata.seconds,'3');
});
test('generic regeneration detaches composition identity before old polling can restore a film',()=>{
    const metadata=utils.clearCompositionMetadata({compositionTaskId:'old',compositionOptions:{title:'old'},compositionStage:'done',compositionDurationSeconds:180,videoTaskId:'h3-new',status:'loading'});
    assert.equal(metadata.compositionTaskId,undefined);assert.equal(metadata.compositionOptions,undefined);assert.equal(metadata.videoTaskId,'h3-new');
    const item={...node('out',''),metadata};assert.equal(utils.applyCompositionTaskUpdate([item],'out',{id:'old',status:'completed',url:'old'})[0],item);
});
test('backend submission waits for durable pending-node acknowledgement and aborts on disk failure',async()=>{
    let acknowledge;let calls=0;const barrier=new Promise(resolve=>{acknowledge=resolve;});
    const api=load('../../web/src/services/api/video-composition.ts',undefined,{fetch:async()=>{calls++;return new Response(JSON.stringify({id:'job',status:'queued',progress:0}));}});
    const pending=api.createCompositionAfterSave({requestId:'job',clips:[]},()=>barrier);
    await Promise.resolve();assert.equal(calls,0);acknowledge();await pending;assert.equal(calls,1);
    await assert.rejects(()=>api.createCompositionAfterSave({requestId:'other',clips:[]},async()=>{throw new Error('disk unavailable');}),/disk unavailable/);
    assert.equal(calls,1);
});
test('successive composition outputs do not cover existing nodes',()=>{
    const source=node('source','video:a');const previous={...node('first','video:b'),position:{x:516,y:0}};
    const position=utils.compositionPosition([source],[source,previous]);
    assert.equal(position.x,516);assert.equal(position.y,312);
});
