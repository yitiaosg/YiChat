(() => {
  'use strict';
  const payload = JSON.parse(document.getElementById('shareData').textContent);
  const record = payload.record;
  const publicUrl = new URL(payload.share_url, location.origin).href;
  const canvas = document.getElementById('poster');
  const ctx = canvas.getContext('2d');
  const status = document.getElementById('imageStatus');
  const shareButton = document.getElementById('shareImage');
  const downloadButton = document.getElementById('downloadImage');
  const money = cents => (cents / 100).toFixed(2);
  const signed = cents => (cents > 0 ? '+' : '') + money(cents);
  const netColor = record.score_cents > 0 ? '#53dda9' : record.score_cents < 0 ? '#ff7185' : '#d1e2f7';
  document.getElementById('shareUrl').textContent = publicUrl;

  function fillRoundRect(x, y, width, height, radius, color) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + width, y, x + width, y + height, radius);
    ctx.arcTo(x + width, y + height, x, y + height, radius);
    ctx.arcTo(x, y + height, x, y, radius);
    ctx.arcTo(x, y, x + width, y, radius);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }

  function text(value, x, y, size, color = '#eef6ff', weight = 600, maxWidth) {
    ctx.fillStyle = color;
    ctx.font = `${weight} ${size}px system-ui, -apple-system, "Microsoft YaHei", sans-serif`;
    ctx.textBaseline = 'alphabetic';
    if (maxWidth === undefined) ctx.fillText(String(value), x, y);
    else ctx.fillText(String(value), x, y, maxWidth);
  }

  function loadImage(src) {
    if (!src) return Promise.resolve(null);
    return new Promise(resolve => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
      image.src = src;
    });
  }

  function metric(label, value, x, y) {
    fillRoundRect(x, y, 425, 142, 20, '#152946');
    text(label, x + 28, y + 49, 25, '#a9c0db', 500);
    text(value, x + 28, y + 107, 43, '#eef6ff', 750, 370);
  }

  function drawQrCode(url) {
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    const count = qr.getModuleCount();
    const x = 764, y = 1074, outer = 256;
    const moduleSize = Math.floor((outer - 44) / count);
    const qrSize = moduleSize * count;
    const startX = x + Math.floor((outer - qrSize) / 2);
    const startY = y + Math.floor((outer - qrSize) / 2);
    fillRoundRect(x, y, outer, outer, 14, '#ffffff');
    ctx.fillStyle = '#07162b';
    for (let row = 0; row < count; row++) {
      for (let col = 0; col < count; col++) {
        if (!qr.isDark(row, col)) continue;
        ctx.fillRect(startX + col * moduleSize, startY + row * moduleSize, moduleSize, moduleSize);
      }
    }
  }

  function drawUrl(url) {
    ctx.font = '500 22px system-ui, -apple-system, "Microsoft YaHei", sans-serif';
    const lines = [];
    let line = '';
    for (const char of url) {
      if (ctx.measureText(line + char).width > 620 && line) {
        lines.push(line);
        line = char;
      } else line += char;
    }
    if (line) lines.push(line);
    for (let i = 0; i < Math.min(3, lines.length); i++) {
      text(lines[i], 92, 1235 + i * 31, 22, '#afcbea', 500);
    }
  }

  async function drawPoster() {
    const [logo, avatar, plane] = await Promise.all([
      loadImage('/static/logo.svg'),
      loadImage(record.avatar_url),
      loadImage('/status/parachute-plane.svg')
    ]);
    const gradient = ctx.createLinearGradient(0, 0, 1080, 1400);
    gradient.addColorStop(0, '#081831');
    gradient.addColorStop(0.55, '#102f58');
    gradient.addColorStop(1, '#061226');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 1080, 1400);
    for (const [x, y, radius] of [[122,104,2],[520,98,2],[640,146,3],[902,212,2],[180,390,2],[948,546,2],[84,771,2],[982,907,2],[580,1030,2]]) {
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fillStyle = '#d5ebff99'; ctx.fill();
    }
    ctx.beginPath();ctx.arc(920, 128, 66, 0, Math.PI * 2);ctx.fillStyle = '#fff0b8';ctx.shadowColor = '#ffdb91';ctx.shadowBlur = 48;ctx.fill();ctx.shadowBlur = 0;
    if (plane) ctx.drawImage(plane, 737, 214, 255, 104);
    if (logo) ctx.drawImage(logo, 80, 76, 64, 64);
    text('PARACHUTE', 162, 124, 35, '#eef6ff', 900);
    text('YITIAO · 飞行成绩', 84, 196, 26, '#9ec6ef', 650);
    fillRoundRect(78, 232, 924, 805, 32, '#0b1a31d9');

    ctx.save();
    ctx.beginPath();ctx.arc(135, 306, 38, 0, Math.PI * 2);ctx.clip();
    if (avatar) ctx.drawImage(avatar, 97, 268, 76, 76);
    else {ctx.fillStyle='#3b83c1';ctx.fillRect(97,268,76,76);text((record.nickname||record.user||'?').slice(0,1).toUpperCase(),112,321,38,'#ffffff',800)}
    ctx.restore();
    text(record.nickname || record.user, 192, 303, 34, '#eef6ff', 800, 750);
    text(record.played_at, 192, 339, 23, '#a9c0db', 500);
    text(record.status === 'jumped' ? '安全跳伞' : '飞机爆炸', 92, 437, 54, '#eef6ff', 850);
    text(signed(record.score_cents), 92, 578, 112, netColor, 900, 850);
    text(`余额净${record.score_cents >= 0 ? '盈利' : '亏损'} · points`, 97, 628, 26, '#b8cde5', 600);
    if (record.status === 'jumped') text(`飞机在 ${Number(record.crash_height).toFixed(2)} 米处爆炸`, 97, 671, 27, '#ffd282', 750);
    metric('飞行高度', `${Number(record.height || 0).toFixed(2)} m`, 92, 706);
    metric('本局投入', money(record.stake_cents), 562, 706);
    metric('跳伞返还', money(record.payout_cents), 92, 874);
    metric('手续费', money(record.fee_cents), 562, 874);
    ctx.fillStyle = '#40658b';ctx.fillRect(84, 1055, 912, 2);
    text('扫码查看公开成绩', 92, 1141, 31, '#eef6ff', 800);
    text('也来玩 Parachute', 92, 1187, 26, '#9ec6ef', 600);
    drawUrl(publicUrl);
    drawQrCode(publicUrl);
    text('yitiao.org/games/parachute/', 90, 1360, 23, '#8eaed0', 550);
  }

  function pngBlob() {
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(Error('图片生成失败')), 'image/png'));
  }

  async function downloadPng(blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `parachute-${record.id}.png`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  let posterBlob = null;
  drawPoster().then(async () => {
    posterBlob = await pngBlob();
    status.textContent = '图片已在浏览器中生成，可以分享或下载。';
    shareButton.disabled = false;
    downloadButton.disabled = false;
  }).catch(error => {
    status.textContent = `图片生成失败：${error.message}`;
  });

  shareButton.addEventListener('click', async () => {
    if (!posterBlob) return;
    shareButton.disabled = true;
    try {
      const file = typeof File === 'undefined' ? null : new File([posterBlob], `parachute-${record.id}.png`, {type:'image/png'});
      if (file && navigator.share && navigator.canShare && navigator.canShare({files:[file]})) {
        try { await navigator.share({files:[file], title:'Parachute 飞行成绩'}); }
        catch (error) { if (error.name !== 'AbortError') await downloadPng(posterBlob); }
      } else await downloadPng(posterBlob);
    } catch (error) { alert(error.message); }
    finally { shareButton.disabled = false; }
  });

  downloadButton.addEventListener('click', async () => {
    if (!posterBlob) return;
    downloadButton.disabled = true;
    try { await downloadPng(posterBlob); }
    catch (error) { alert(error.message); }
    finally { downloadButton.disabled = false; }
  });

  document.getElementById('copyLink').addEventListener('click', async function () {
    try { await navigator.clipboard.writeText(publicUrl); this.textContent = '链接已复制'; }
    catch { window.prompt('复制分享链接', publicUrl); }
  });
})();
