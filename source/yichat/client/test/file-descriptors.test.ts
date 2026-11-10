import test from 'node:test';import assert from 'node:assert/strict';
import {rememberFiles,checkFile} from '../src/file-descriptors';import {sha256} from '../src/packet-protocol';
test('remembering an absent file stores only its descriptor; downloaded and cached bytes must match',async()=>{const bytes=new TextEncoder().encode('saved original photo'),id='e'.repeat(48);rememberFiles([{id,sha256:await sha256(bytes),bytes:String(bytes.length)}]);assert.equal((await checkFile(id,new Blob([bytes]))).size,bytes.length);await assert.rejects(checkFile(id,new Blob(['wrong photo'])))});
