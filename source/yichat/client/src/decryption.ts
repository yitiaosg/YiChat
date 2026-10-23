import type {MatrixClient} from 'matrix-js-sdk';
import type {CryptoBackend} from 'matrix-js-sdk/lib/common-crypto/CryptoBackend';

// In this pinned SDK decryptEventIfNeeded skips events whose clearEvent already
// contains m.bad.encrypted. Use the SDK's real retry path, preserving its key
// verification, replay checks and decrypted-event notification.
export async function retryFailedDecryptions(client:Pick<MatrixClient,'getRooms'|'getCrypto'>){
 const backend=client.getCrypto() as unknown as CryptoBackend|undefined;
 const result={retried:0,recovered:0,failed:0};
 if(!backend)return result;
 for(const room of client.getRooms())for(const event of room.getLiveTimeline().getEvents()){
  if(!event.isDecryptionFailure()||event.isRedacted()||event.isBeingDecrypted())continue;
  result.retried++;
  await event.attemptDecryption(backend,{isRetry:true}).catch(()=>{});
  if(event.isDecryptionFailure())result.failed++;else result.recovered++;
 }
 return result;
}
