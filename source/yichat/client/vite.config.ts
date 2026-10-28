import { defineConfig } from 'vite';
export default defineConfig({base:'/',build:{rollupOptions:{input:{main:'index.html',notification:'notification.html'}},target:'es2022',assetsInlineLimit:0,chunkSizeWarningLimit:1800},server:{proxy:{'/api':'http://127.0.0.1:5018','/_matrix':'http://127.0.0.1:5018','/auth':'http://127.0.0.1:5018'}}});
