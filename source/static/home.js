(() => {
  const body = document.body;
  const content = document.getElementById('homeContent');
  const panels = Array.from(document.querySelectorAll('.panel'));
  const dots = Array.from(document.querySelectorAll('.dot'));
  const menuItems = Array.from(document.querySelectorAll('.menu-item'));
  const menuBtn = document.getElementById('menuBtn');
  const menuPanel = document.getElementById('menuPanel');
  const introOverlay = document.getElementById('introOverlay');
  let current = 0;

  function setActive(index) {
    if (index < 0 || index >= panels.length) return;
    current = index;
    panels.forEach((panel, i) => panel.classList.toggle('active', i === index));
    dots.forEach((dot, i) => {
      dot.classList.toggle('active', i === index);
      if (i === index) dot.setAttribute('aria-current', 'true');
      else dot.removeAttribute('aria-current');
    });
    menuItems.forEach((item, i) => item.classList.toggle('active', i === index));
  }

  function openMenu(open) {
    menuPanel.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
    menuPanel.setAttribute('aria-hidden', String(!open));
  }

  function scrollToPanel(index) {
    if (!panels[index]) return;
    panels[index].scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start'
    });
    setActive(index);
    openMenu(false);
  }

  menuBtn.addEventListener('click', () => openMenu(!menuPanel.classList.contains('open')));
  menuItems.forEach((item, index) => item.addEventListener('click', () => scrollToPanel(index)));
  dots.forEach((dot, index) => dot.addEventListener('click', () => scrollToPanel(index)));
  document.addEventListener('click', (event) => {
    if (!menuPanel.contains(event.target) && !menuBtn.contains(event.target)) openMenu(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') openMenu(false);
    if (event.target.closest('a, button, input, textarea, select')) return;
    if (event.key === 'ArrowDown' || event.key === 'PageDown') {
      event.preventDefault();
      scrollToPanel(current + 1);
    } else if (event.key === 'ArrowUp' || event.key === 'PageUp') {
      event.preventDefault();
      scrollToPanel(current - 1);
    }
  });

  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting);
    if (!visible.length) return;
    visible.sort((a, b) => b.intersectionRatio - a.intersectionRatio);
    setActive(panels.indexOf(visible[0].target));
  }, { root: content, threshold: [0.4, 0.55, 0.7] });
  panels.forEach((panel) => observer.observe(panel));

  setActive(0);
  openMenu(false);
  requestAnimationFrame(() => {
    body.classList.add('ready');
    window.setTimeout(() => introOverlay.remove(), 900);
  });
})();
