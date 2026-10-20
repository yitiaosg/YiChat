import type {Logger} from 'matrix-js-sdk/lib/logger';
// Keep SDK debug traffic, account IDs and request objects out of the console.
// Actionable network/crypto failures are reported by the UI and browser network log.
const quiet=()=>{};
export const sdkLog:Logger={trace:quiet,debug:quiet,info:quiet,warn:quiet,error:quiet,getChild:()=>sdkLog};
