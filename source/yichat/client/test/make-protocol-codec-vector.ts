import {readFile,writeFile} from 'node:fs/promises';
import {bundleDays,encodePackage} from '../src/packet-protocol';
const v=JSON.parse(await readFile(new URL('../../tests/fixtures/protocol-v2-vectors.json',import.meta.url),'utf8'));
const blob=await encodePackage(bundleDays([v.packets.special]));await writeFile(new URL('../../../../android/app/src/test/resources/protocol-v2-browser.ycp',import.meta.url),new Uint8Array(await blob.arrayBuffer()));
