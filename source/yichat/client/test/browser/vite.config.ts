// Dedicated loopback fixture. Never proxy test credentials to the live gateway.
import {defineConfig} from 'vite';
const gateway='http://127.0.0.1:18018';
const proxy=Object.fromEntries(['/api','/_matrix','/auth'].map(path=>[path,{target:gateway,
 configure(server:any){server.on('proxyReq',(request:any)=>request.setHeader('origin',gateway))}}]));
export default defineConfig({root:process.cwd(),
 build:{outDir:'test-results/notification-browser',emptyOutDir:true,target:'es2022',assetsInlineLimit:0,chunkSizeWarningLimit:1800,
  rollupOptions:{input:{test:'test/browser/notification.html',notification:'notification.html'}}},
 server:{host:'127.0.0.1',port:5173,strictPort:true,proxy},
 preview:{host:'127.0.0.1',port:5173,strictPort:true,proxy}});
