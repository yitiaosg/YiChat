import {Shield,FileArchive,HardDrive,Bell,Palette,Sun,Moon,Monitor,ChevronRight,FolderInput,ExternalLink,RefreshCw} from 'lucide-preact';
import {Modal} from './shared';import {isApp} from './app';
export function YiChatSettings({close,open,notifications}:{close:()=>void;open:(page:string)=>void;notifications:boolean}){
 const entry=(page:string,label:string,description:string,Icon:any)=><button class="settings-entry" aria-label={label} onClick={()=>open(page)}><Icon size={19}/><span><strong>{label}</strong><small>{description}</small></span><ChevronRight size={16}/></button>;
 return <Modal title="Yichat设置" close={close}>
 {(!isApp||notifications)&&<section class="settings-category"><h3>消息提醒</h3>{entry(isApp?'notification-settings':'web-notifications',isApp?'消息通知':'浏览器通知','通知权限与后台提醒',Bell)}</section>}
 <section class="settings-category"><h3>聊天记录</h3>{isApp?<>{entry('packet-export','导出聊天记录','选择本机会话与 GMT 日期，离线导出',FileArchive)}{entry('packet-import','导入聊天记录','从数据包恢复本机聊天记录',FolderInput)}{entry('app-settings','自动备份与定期导出','查看本机保存状态，设置外部导出目录',HardDrive)}</>:<a class="settings-entry" aria-label="数据包合并与核验" href="/data-packages" target="_blank" rel="noopener noreferrer"><FileArchive size={19}/><span><strong>数据包合并与核验</strong><small>在独立页面整理与核验聊天记录</small></span><ExternalLink size={16}/></a>}</section>
 <section class="settings-category"><h3>账户与数据安全</h3>{entry('security','安全与备份','账户、设备与本机数据保护',Shield)}</section>
 {isApp&&<section class="settings-category"><h3>应用</h3>{entry('app-updates','应用更新','检查并安装新版本',RefreshCw)}</section>}
 </Modal>
}
export function ThemeSettings({theme,change,colors,close}:{theme:string;change:(value:string)=>void;colors:()=>void;close:()=>void}){
 return <Modal title="主题" close={close}><section class="settings-category"><h3>外观模式</h3><div class="theme-options">{[['system','跟随系统',Monitor],['light','浅色',Sun],['dark','深色',Moon]].map(([key,name,Icon]:any)=><button aria-pressed={theme===key} class={theme===key?'selected':''} onClick={()=>change(key)}><Icon size={20}/>{name}</button>)}</div></section>{isApp&&<section class="settings-category"><h3>主题颜色</h3><button class="settings-entry" onClick={colors}><Palette size={19}/><span>主题色设置</span><ChevronRight size={16}/></button><p class="small muted">会话背景与单独主题色可在会话右上角设置。</p></section>}</Modal>
}
