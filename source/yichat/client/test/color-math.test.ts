import test from 'node:test';import assert from 'node:assert/strict';
import {hexToHsv,hsvToHex,parseHex,validHex} from '../src/color-math';
test('hex colours survive the editor round trip',()=>{
 for(const hex of ['#1479b8','#000000','#ffffff','#d9483b','#6c5ce7','#0f9f8f','#808080','#ff00ff'])assert.equal(hsvToHex(hexToHsv(hex)),hex);
 assert.deepEqual(hexToHsv('#ff0000'),{h:0,s:1,v:1});assert.equal(hsvToHex({h:120,s:1,v:1}),'#00ff00');assert.equal(hsvToHex({h:359.9,s:0,v:.5}),'#808080');
});
test('typed colour values are normalised or rejected',()=>{
 assert.equal(parseHex(' 1479B8 '),'#1479b8');assert.equal(parseHex('#18b'),'#1188bb');
 for(const bad of ['','#12','#12345','#gggggg','rgb(1,2,3)','#1234567'])assert.equal(parseHex(bad),undefined);
 assert.equal(validHex('#1479B8'),true);assert.equal(validHex('1479b8'),false);
});
