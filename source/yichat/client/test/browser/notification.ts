// Opt-in test entry, excluded from Vite's production inputs and APK assets.
import {chat} from '../../src/chat';
(window as any).__notificationTestChat=chat;
void chat.boot();
