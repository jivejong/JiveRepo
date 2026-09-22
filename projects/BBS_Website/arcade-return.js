(() => {
  const params = new URLSearchParams(window.location.search);
  const mode = params.get('mode') === 'mouse' ? 'mouse' : 'keyboard';

  window.returnToBBSMenu = function returnToBBSMenu() {
    const menuUrl = new URL(`../index.html?menu=1&mode=${mode}`, window.location.href);
    window.top.location.href = menuUrl.href;
  };

  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.returnToBBSMenu();
  }, true);
})();
