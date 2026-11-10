import {useEffect,useState} from 'preact/hooks';
import {appCall} from './app';
import {chat} from './chat';
import {Modal,ErrorText} from './shared';
export function AppSettings({close,audit}:{close:()=>void;audit:()=>void}){
 const [status,setStatus]=useState<any>(),[days,setDays]=useState('1'),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const refresh=async()=>{const data=await appCall('backupStatus');setStatus(data);if(data.days)setDays(String(data.days))};
 useEffect(()=>{void refresh().catch(e=>setError(e.message))},[]);
 async function run(method:string,args={}){setBusy(true);setError('');try{const result=await appCall(method,args);if(!result?.cancelled){setPassword('');await refresh()}}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <Modal title="App 数据备份与导出" close={close}>
  <p>聊天网页与浏览器共用同一套界面。此 App 的加密存储、缓存和数据包独立保存在应用内部，不与系统浏览器混用。</p>
  <p class="notice">自动保存原消息包到内部 data 目录，无需选择文件夹。卸载会删除内部数据。消息不再依赖会话密钥。</p>
  {status&&<><p role="status">{status.status}</p><p>内部已保存 {status.packets} 份原消息包</p>{status.pendingMessages>0&&<p>{status.pendingMessages} 条最新消息已加密保存到本机，等待完成服务器校验。</p>}<p>{status.exportStatus}</p></>}
  <button type="button" disabled={busy} onClick={()=>run('backupNow')}>立即保存内部快照</button>
  <h3>App 更新</h3><UpdateControls/>
  {chat.appNotifications&&<><h3>消息通知</h3><NotificationControls/></>}
  <h3>核验聊天记录</h3><p class="small muted">选择连续最多7个 GMT 整日，与服务器存证比对后只读查看，无需会话密钥。</p><button type="button" disabled={busy} onClick={audit}>验证备份记录</button>
  <h3>按天导出到外部</h3><label>间隔天数<input type="number" min="1" max="3650" step="1" value={days} onInput={e=>setDays(e.currentTarget.value)}/></label>
  <label>独立导出口令<input type="password" autoComplete="new-password" value={password} onInput={e=>setPassword(e.currentTarget.value)} placeholder="至少12字符，请另行保存"/></label>
  <button type="button" disabled={busy||!/^\d+$/.test(days)||Number(days)<1||Number(days)>3650||password.length<12} onClick={()=>run('configureExport',{days:Number(days),password})}>选择系统目录并启用定期导出</button>
  {status?.days&&<div class="actions"><button type="button" disabled={busy} onClick={()=>run('exportNow')}>立即导出到已选目录</button><button type="button" disabled={busy} onClick={()=>run('stopExport')}>停用外部导出</button></div>}
  <p class="small muted">导出为独立口令加密的 YCP 整日数据包，包含已保存的原始特殊消息和附件，可在网页合并、校验与 App 导入。导出不包含浏览器登录令牌或 Matrix 私钥。系统会在条件允许时按所选天数执行，应用内缓存不会因此清理。</p>
  {busy&&<p role="status">正在处理，请完成系统文件选择…</p>}{error&&<ErrorText>{error}</ErrorText>}
 </Modal>
}

export function NotificationSettings({close}:{close:()=>void}){return <Modal title="消息通知" close={close}><NotificationControls/></Modal>}
function NotificationControls(){
 const [notifications,setNotifications]=useState<any>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function refresh(){try{setNotifications(await appCall('notificationStatus'))}catch(e:any){setError(e.message)}}
 useEffect(()=>{void refresh();const wake=()=>void refresh();window.addEventListener('focus',wake);return()=>window.removeEventListener('focus',wake)},[]);
 async function run(method:string,args={}){setBusy(true);setError('');try{await appCall(method,args);await refresh()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <>  {notifications&&<><label class="check"><input type="checkbox" checked={!!notifications.enabled} disabled={busy} onChange={e=>{const enabled=e.currentTarget.checked;setNotifications({...notifications,enabled});void run('configureNotifications',{enabled})}}/>启用 App 通知</label><p class="small muted">{notifications.permitted?'系统通知权限已开启':'请允许系统通知权限' }。归档和免打扰会话不通知；锁屏隐藏消息正文；解锁后可显示本机解密的内容。{notifications.background_supported?'配置推送后使用系统推送接收；未配置时保留后台连接。系统权限和省电设置可能影响投递。':'此 App 版本的后台通知约15分钟检查一次，建议更新 App。'}</p>{notifications.background_supported&&<button type="button" onClick={()=>void run('backgroundSettings')}>后台运行设置</button>}{!notifications.permitted&&<button type="button" onClick={()=>void run('notificationSettings')}>打开系统通知设置</button>}</>}{!notifications&&!error&&<p role="status">正在读取通知权限…</p>}{error&&<ErrorText>{error}</ErrorText>}</>;
}

function UpdateControls(){
 const [state,setState]=useState<{current?:string;available?:boolean;latest?:string|null;error?:string}>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function check(){setBusy(true);setError('');try{setState(await appCall('checkUpdate'))}catch(e:any){setError(/不支持此 App 操作/.test(e.message)?'此 App 版本不支持应用内检查，请从 GitHub Releases 下载最新版':e.message)}finally{setBusy(false)}}
 return <><p class="small muted">从 GitHub Releases 检查新版本；有更新时在浏览器中下载安装包并覆盖安装，聊天与备份数据保留。</p>
  {state&&<p role="status">{state.error?state.error:state.available?'发现新版本 '+state.latest+'（当前 '+state.current+'）':'已是最新版本 '+state.current}</p>}
  <button type="button" disabled={busy} onClick={check}>{busy?'正在检查…':'检查更新'}</button>{error&&<ErrorText>{error}</ErrorText>}</>
}
