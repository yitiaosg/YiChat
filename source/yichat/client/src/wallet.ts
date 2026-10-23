import {useCallback,useEffect,useRef,useState} from 'preact/hooks';
import {requests} from './api';

export type WalletState={
  enabled:boolean;
  binding:string|null;
  bind_url:string;
  fees:{incoming:string;outgoing_per_share:string};
};

// Binding can change in YIDT while this Web/App dialog stays mounted.
export function useWallet(){
  const [wallet,setWallet]=useState<WalletState>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const mounted=useRef(false);
  const pending=useRef<Promise<WalletState>|null>(null);
  const refresh=useCallback(()=>{
    if(pending.current)return pending.current;
    if(mounted.current){setBusy(true);setError('')}
    const task=requests.api<WalletState>('/api/wallet').then(value=>{
      if(mounted.current)setWallet(value);
      return value;
    }).catch((cause:Error)=>{
      if(mounted.current)setError(cause.message);
      throw cause;
    }).finally(()=>{
      pending.current=null;
      if(mounted.current)setBusy(false);
    });
    pending.current=task;
    return task;
  },[]);
  useEffect(()=>{
    mounted.current=true;
    const resume=()=>{if(!document.hidden)void refresh().catch(()=>{})};
    resume();
    window.addEventListener('focus',resume);
    document.addEventListener('visibilitychange',resume);
    return()=>{
      mounted.current=false;
      window.removeEventListener('focus',resume);
      document.removeEventListener('visibilitychange',resume);
    };
  },[refresh]);
  return {wallet,error,busy,refresh};
}
