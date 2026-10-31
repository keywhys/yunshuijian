/* ============================================================
   云水间 · 听音   ——  前端逻辑
   ============================================================ */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const audio = $('#audio');

  const CN_NUM = ['零','一','二','三','四','五','六','七','八','九'];
  function cnNum(n) {
    if (n <= 0) return '';
    if (n < 10) return CN_NUM[n];
    if (n < 20) return '十' + (n % 10 ? CN_NUM[n % 10] : '');
    if (n < 100) {
      const t = Math.floor(n / 10), o = n % 10;
      return CN_NUM[t] + '十' + (o ? CN_NUM[o] : '');
    }
    return String(n);
  }

  function fmt(s) {
    if (!isFinite(s) || s < 0) return '0:00';
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  // ---------------------------------------------------------------- 状态
  const state = {
    view: 'like',          // like | cloud
    local: [],
    cloud: [],
    queue: [],             // 当前播放队列（歌曲对象数组）
    idx: -1,
    current: null,
    mode: 'order',         // order | single | shuffle
    volume: 0.8,
    muted: false,
    lyricLines: [],
    lyricKey: '',
    lyricTimer: null,
    resolving: false,
    shuffleHistory: [],
    favIds: new Set(),     // "source:id" 集合
    warmed: new Set(),     // 已预热过的封面 pic_id
  };

  // 歌曲对象统一结构：
  // 本地：{kind:'local', id, title, artist, duration, kbps, hasCover}
  // 云端：{kind:'cloud', id, source, title, artist, album, pic_id}

  // ---------------------------------------------------------------- 收藏
  const HEART_SVG = '<svg viewBox="0 0 24 24" class="heart-ico"><path d="M12 20.4C6.8 16.6 3.4 13.3 3.4 9.6 3.4 6.9 5.5 4.9 8.1 4.9c1.5 0 3 .7 3.9 2 .9-1.3 2.4-2 3.9-2 2.6 0 4.7 2 4.7 4.7 0 3.7-3.4 7-8.6 10.8z"/></svg>';

  function favKeyOf(song) {
    return (song.source || 'netease') + ':' + song.id;
  }
  function isLiked(song) {
    if (song.kind === 'local') return true;
    return state.favIds.has(favKeyOf(song));
  }

  function updateFavUI() {
    [...listEl.children].forEach((row, i) => {
      const song = currentList()[i];
      const fb = row.querySelector('.fav-btn');
      if (!fb || !song) return;
      const liked = isLiked(song);
      fb.classList.toggle('liked', liked);
      fb.title = liked ? '已在喜欢中，点击移除' : '加入我的喜欢';
    });
    syncPlayerFav();
  }

  function syncPlayerFav() {
    const btn = $('#btnFav');
    if (!btn) return;
    const song = state.current;
    const liked = song ? isLiked(song) : false;
    btn.classList.toggle('liked', liked);
    btn.classList.toggle('hide', !song || song.kind === 'local');
    btn.title = song && song.kind === 'local'
      ? '本地曲目常驻喜欢' : (liked ? '已在喜欢中' : '加入我的喜欢');
  }

  const btnFavEl = document.getElementById('btnFav');
  if (btnFavEl) {
    btnFavEl.addEventListener('click', () => {
      const song = state.current;
      if (!song) { toast('先放一首歌'); return; }
      if (song.kind === 'local') { toast('本地曲目常驻喜欢'); return; }
      toggleFav(song);
    });
  }

  async function toggleFav(song) {
    const liked = !isLiked(song);
    try {
      const r = await fetch('/api/fav/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: song.id, source: song.source || 'netease',
          title: song.title, artist: song.artist || '',
          album: song.album || '', pic_id: song.pic_id || '',
          liked,
        }),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || '操作失败');
      state.favIds = new Set(d.favIds || []);
      updateFavUI();
      toast(liked ? '已加入我的喜欢' : '已从喜欢中移除');
      // 无论当前在哪个视图，都要把「我的喜欢」的数据同步过来
      await loadLocal(true, true);
    } catch (e) {
      toast(e.message || '操作失败');
    }
  }

  // ---------------------------------------------------------------- 提示
  let toastTimer = null;
  function toast(msg, ms = 2400) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), ms);
  }

  // ---------------------------------------------------------------- 列表渲染
  const listEl = $('#list');
  const emptyEl = $('#empty');
  const loadingEl = $('#loading');

  function currentList() {
    return state.view === 'like' ? state.local : state.cloud;
  }

  function renderList() {
    const arr = currentList();
    listEl.innerHTML = '';
    const frag = document.createDocumentFragment();

    arr.forEach((song, i) => {
      const row = document.createElement('div');
      row.className = 'row';
      row.dataset.i = i;
      const isCur = state.current &&
        state.current.kind === song.kind &&
        state.current.id === song.id;
      if (isCur) {
        row.classList.add('active');
        if (!audio.paused) row.classList.add('playing');
      }

      const no = document.createElement('div');
      no.className = 'no';
      no.innerHTML = `<span class="no-txt">${i + 1}</span>
        <span class="eq"><i></i><i></i><i></i></span>`;

      const ti = document.createElement('div');
      ti.className = 'ti';
      const nm = document.createElement('span');
      nm.className = 'name';
      nm.textContent = song.title || song.file || '无题';
      ti.appendChild(nm);
      if (song.kind === 'local' && song.kbps) {
        const q = document.createElement('span');
        q.className = 'q';
        q.textContent = song.kbps + 'K';
        ti.appendChild(q);
      }

      const ar = document.createElement('div');
      ar.className = 'ar';
      ar.textContent = song.artist || '未署';

      const du = document.createElement('div');
      du.className = 'du';
      du.textContent = song.duration ? fmt(song.duration) : '—';

      const cov = document.createElement('div');
      cov.className = 'cov';
      cov.style.width = '32px';
      cov.style.height = '32px';
      const cu = coverUrlOf(song);
      if (cu) {
        const im = new Image();
        im.loading = 'lazy';
        im.src = cu;
        im.onerror = function () {
          cov.innerHTML = '';
          cov.classList.add('empty');
          cov.style.width = '32px';
          cov.style.height = '32px';
        };
        cov.appendChild(im);
      } else {
        cov.classList.add('empty');
      }

      row.append(no, cov, ti, ar, du);

      // 云端曲：喜欢按钮
      const act = document.createElement('div');
      act.className = 'act';
      if (song.kind === 'cloud') {
        const fb = document.createElement('button');
        fb.className = 'fav-btn' + (isLiked(song) ? ' liked' : '');
        fb.innerHTML = HEART_SVG;
        fb.title = isLiked(song) ? '已在喜欢中，点击移除' : '加入我的喜欢';
        fb.addEventListener('click', (e) => {
          e.stopPropagation();
          toggleFav(song);
        });
        act.appendChild(fb);
      }
      row.appendChild(act);

      row.addEventListener('click', () => {
        state.queue = currentList().slice();
        playIndex(i);
      });
      frag.appendChild(row);
    });

    listEl.appendChild(frag);
    warmCovers(arr);
    emptyEl.hidden = arr.length > 0;
    if (arr.length === 0 && state.view === 'cloud' && !loadingEl.hidden === false) {
      // 空态交给 empty 处理
    }
    updateHead();
  }

  // 封面预热：让后端并发把封面抓到本地缓存，之后打开就是读盘
  function warmCovers(arr) {
    const ids = [];
    arr.forEach(function (s) {
      if (s && s.kind !== 'local' && s.pic_id && !state.warmed.has(s.pic_id)) {
        state.warmed.add(s.pic_id);
        ids.push(s.pic_id);
      }
    });
    if (!ids.length) return;
    // 延迟 5 秒再预热，避免和首屏可见封面抢带宽
    setTimeout(function () {
      fetch('/api/covers/warm?size=80&ids=' + encodeURIComponent(ids.slice(0, 80).join(',')))
        .catch(function () {});
    }, 5000);
  }

  function updateHead() {
    const isLike = state.view === 'like';
    $('#viewTitle').textContent = isLike ? '我的喜欢' : '云上寻音';
    if (isLike) {
      const nLocal = state.local.filter(s => s.kind === 'local').length;
      const nFav = state.local.length - nLocal;
      $('#viewSub').textContent = state.local.length
        ? `本地 ${nLocal} 曲 · 云端收藏 ${nFav} 曲`
        : '尚未扫得曲目';
      $('#cntLike').textContent = state.local.length;
    } else {
      const q = ($('#q').value || '').trim();
      $('#viewSub').textContent = state.cloud.length
        ? `「${q || '云端'}」得 ${state.cloud.length} 曲，择优取无损`
        : '于搜索框输入曲名，探云端高质音源';
      $('#cntCloud').textContent = state.cloud.length || '—';
    }
    emptyEl.querySelector('.empty-v') &&
      (emptyEl.querySelector('.empty-v').innerHTML =
        isLike ? '此处<br>无曲' : '云深<br>无音');
  }

  function markActive() {
    [...listEl.children].forEach((row, i) => {
      const song = currentList()[i];
      const on = state.current && song &&
        state.current.kind === song.kind && state.current.id === song.id;
      row.classList.toggle('active', !!on);
      row.classList.toggle('playing', !!on && !audio.paused);
    });
  }

  // ---------------------------------------------------------------- 视图切换
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.view = btn.dataset.view;
      renderList();
    });
  });

  // ---------------------------------------------------------------- 播放核心
  function setBadge(text) {
    const b = $('#qBadge');
    if (!text) { b.hidden = true; return; }
    b.hidden = false;
    b.textContent = text;
  }

  function setMeta(song) {
    const title = song ? (song.title || '无题') : '未择曲';
    const artist = song ? (song.artist || '未署') : '静候一曲';
    $('#pTitle').textContent = title;
    $('#pArtist').textContent = song && song.album ? artist + ' · ' + song.album : artist;
    const nt = $('#npTitle'), na = $('#npArtist');
    if (nt) nt.textContent = title;
    if (na) na.textContent = artist;
    document.title = song ? title + ' — 云水间' : '云水间 · 听音';
  }

  function coverUrlOf(song) {
    if (!song) return '';
    if (song.kind === 'local' && song.hasCover) return '/api/cover/local/' + song.id;
    if (song.pic_id) return '/api/cover?id=' + encodeURIComponent(song.pic_id) + '&size=80';
    return '';
  }

  function setCover(song) {
    const faces = [$('#discFace'), $('#npCover')].filter(Boolean);
    let url = '';
    if (song && song.kind === 'local' && song.hasCover) {
      url = '/api/cover/local/' + song.id;
    } else if (song && song.pic_id) {
      url = '/api/cover?id=' + encodeURIComponent(song.pic_id) + '&size=500';
    }
    faces.forEach(function (face) {
      face.innerHTML = '';
      if (url) {
        const img = new Image();
        img.onerror = function () {
          const pic = song && song.pic_id;
          if (pic && img.dataset.fb !== '1') {
            img.dataset.fb = '1';
            img.src = '/api/cover?id=' + encodeURIComponent(pic) + '&size=80';
            return;
          }
          face.innerHTML = placeholderSvg();
        };
        img.src = url;
        face.appendChild(img);
      } else {
        face.innerHTML = placeholderSvg();
      }
    });
  }
  function placeholderSvg() {
    return `<svg viewBox="0 0 64 64" class="disc-ph">
      <path d="M44 14a20 20 0 1 0 0 36 17 17 0 0 1 0-36z" fill="currentColor" opacity=".5"/></svg>`;
  }

  /** 彻底断开当前音频流：切歌时不清理会让旧连接挂住服务端线程 */
  function hardStopAudio() {
    try {
      state.releasing = true;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    } catch (_) {}
    setTimeout(() => { state.releasing = false; }, 300);
  }

  async function playIndex(i) {
    const arr = state.queue.length ? state.queue : currentList();
    if (!arr.length) return;
    if (i < 0) i = arr.length - 1;
    if (i >= arr.length) i = 0;
    state.idx = i;
    const song = arr[i];
    hardStopAudio();
    state.current = song;
    setMeta(song);
    setCover(song);
    markActive();
    syncPlayerFav();
    setBadge(song.kind === 'local' ? (song.kbps ? song.kbps + 'K · 本地' : '本地') : '探寻中');
    loadLyric(song);

    try {
      if (song.kind === 'local') {
        audio.src = `/api/stream/local/${song.id}`;
        audio.play().catch(() => {});
      } else {
        state.resolving = true;
        $('#btnPlay').classList.remove('on');
        const r = await fetch(`/api/url?id=${encodeURIComponent(song.id)}&source=${encodeURIComponent(song.source || 'netease')}`);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || '无可用音源');
        if (state.current !== song) return; // 用户已切歌
        audio.src = `/api/proxy?u=${encodeURIComponent(d.url)}`;
        setBadge(d.quality ? d.quality + (d.br ? ' · ' + d.br + 'kbps' : '') : '云端');
        await audio.play();
      }
    } catch (e) {
      state.resolving = false;
      state.failStreak = (state.failStreak || 0) + 1;
      toast(e.message || '播放失败，换个版本试试');
      setBadge('无音源');
      $('#btnPlay').classList.remove('on');
      // 云端无声源时自动跳下一首；连续失败则停下，避免无谓循环
      if (song.kind === 'cloud' && arr.length > 1) {
        if (state.failStreak >= 4) {
          state.failStreak = 0;
          toast('连续多曲无可用音源，换首歌或换个版本试试', 3600);
        } else {
          setTimeout(() => next(), 900);
        }
      }
    } finally {
      state.resolving = false;
    }
  }

  function next(auto = false) {
    if (!state.queue.length) return;
    if (state.mode === 'shuffle' && state.queue.length > 1) {
      let n = Math.floor(Math.random() * state.queue.length);
      if (n === state.idx) n = (n + 1) % state.queue.length;
      playIndex(n);
      return;
    }
    if (auto && state.mode === 'single') {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    playIndex(state.idx + 1);
  }

  function prev() {
    if (!state.queue.length) return;
    if (audio.currentTime > 3) { audio.currentTime = 0; return; }
    playIndex(state.idx - 1);
  }

  // ---------------------------------------------------------------- 播放控制 UI
  const icoPlay = `<path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/>`;
  const icoPause = `<path d="M8 5v14M16 5v14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/>`;

  function syncPlayIcon() {
    const playing = !audio.paused && !audio.ended;
    const np = $('#nowPlaying');
    if (np) np.classList.toggle('playing', playing);
    $('#icoPlay').innerHTML = playing ? icoPause : icoPlay;
    $('#disc').classList.toggle('playing', playing);
    $('#disc').classList.toggle('paused', !playing);
    $('#btnPlay').classList.toggle('on', playing);
    markActive();
  }

  $('#btnPlay').addEventListener('click', () => {
    if (!state.current) {
      if (state.queue.length === 0) state.queue = currentList().slice();
      if (state.queue.length) { playIndex(0); return; }
      toast('暂无曲目');
      return;
    }
    if (audio.paused) {
      // src 被清掉或已出错时，直接重播当前曲，避免"点了没反应"
      if (!audio.getAttribute('src') || audio.error) {
        playIndex(state.idx >= 0 ? state.idx : 0);
        return;
      }
      const p = audio.play();
      if (p && p.catch) {
        p.catch(() => { playIndex(state.idx >= 0 ? state.idx : 0); });
      }
    } else {
      audio.pause();
    }
  });

  $('#btnNext').addEventListener('click', () => next());
  $('#btnPrev').addEventListener('click', () => prev());

  // ---- 播放模式图标（仿网易云：列表循环 / 单曲循环 / 随机）----
  const MODE_SVGS = {
    order: '<svg viewBox="0 0 24 24"><path d="M4.5 12a7.5 7.5 0 0 1 13-5.1M19.5 12a7.5 7.5 0 0 1-13 5.1"/>'
         + '<path d="M17.2 3.4v3.6h-3.6M6.8 20.6V17h3.6"/></svg>',
    single: '<svg viewBox="0 0 24 24"><path d="M4.5 12a7.5 7.5 0 0 1 13-5.1M19.5 12a7.5 7.5 0 0 1-13 5.1"/>'
          + '<path d="M17.2 3.4v3.6h-3.6M6.8 20.6V17h3.6"/>'
          + '<text x="12" y="15" text-anchor="middle" font-size="9" font-weight="700" fill="currentColor" stroke="none">1</text></svg>',
    shuffle: '<svg viewBox="0 0 24 24"><path d="M3 7h3.1c4.9 0 5.9 8 10.8 8H21M3 17h3.1c1.9 0 3.2-1.2 4.4-2.7M14 9.7C15.2 8.2 16.4 7 18.3 7H21"/>'
           + '<path d="M18.7 4.4L21.2 7l-2.5 2.6M18.7 14.4L21.2 17l-2.5 2.6"/></svg>',
  };
  function syncModeIcon() {
    const btn = $('#btnMode');
    btn.innerHTML = MODE_SVGS[state.mode] || MODE_SVGS.order;
    const label = { order: '列表循环', single: '单曲循环', shuffle: '随机播放' }[state.mode];
    btn.classList.toggle('on', state.mode !== 'order');
    btn.title = label;
    return label;
  }

  $('#btnMode').addEventListener('click', () => {
    const order = ['order', 'single', 'shuffle'];
    state.mode = order[(order.indexOf(state.mode) + 1) % 3];
    toast(syncModeIcon());
  });

  // 尽听 / 乱序
  $('#btnPlayAll').addEventListener('click', () => {
    const arr = currentList();
    if (!arr.length) { toast(state.view === 'like' ? '曲库空空' : '先去寻一曲'); return; }
    state.queue = arr.slice();
    state.mode = 'order';
    syncModeIcon();
    playIndex(0);
  });
  $('#btnShuffle').addEventListener('click', () => {
    const arr = currentList();
    if (!arr.length) { toast(state.view === 'like' ? '曲库空空' : '先去寻一曲'); return; }
    const q = arr.slice();
    for (let i = q.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [q[i], q[j]] = [q[j], q[i]];
    }
    state.queue = q;
    state.mode = 'shuffle';
    syncModeIcon();
    toast('乱序 · 随缘而听');
    playIndex(0);
  });

  // ---------------------------------------------------------------- 音频事件
  audio.addEventListener('timeupdate', () => {
    const d = audio.duration || (state.current && state.current.duration) || 0;
    const c = audio.currentTime || 0;
    const p = d ? (c / d) * 100 : 0;
    $('#barFill').style.width = p + '%';
    $('#barDot').style.left = p + '%';
    $('#tCur').textContent = fmt(c);
    if (d) $('#tDur').textContent = fmt(d);
    syncLyric(c);
  });

  audio.addEventListener('loadedmetadata', () => {
    if (state.current && isFinite(audio.duration)) {
      state.current.duration = audio.duration;
      $('#tDur').textContent = fmt(audio.duration);
      // 云端曲目补上时长显示
      if (state.current.kind === 'cloud') {
        const arr = currentList();
        const i = arr.findIndex(s => s.kind === 'cloud' && s.id === state.current.id);
        if (i >= 0) { arr[i].duration = audio.duration; }
        const row = listEl.children[state.idx];
        if (row) { const du = row.querySelector('.du'); if (du) du.textContent = fmt(audio.duration); }
      }
    }
  });

  audio.addEventListener('play', () => { state.failStreak = 0; syncPlayIcon(); });
  audio.addEventListener('pause', syncPlayIcon);
  audio.addEventListener('ended', () => next(true));
  audio.addEventListener('error', () => {
    if (state.releasing) return;          // 主动释放产生的空 src，忽略
    if (!audio.getAttribute('src')) return;
    if (!state.current) return;
    syncPlayIcon();
    if (state.current.kind === 'local') toast('此曲读取失败，文件或已损坏');
    else toast('云端音源中断，可换一首或稍后重试');
  });

  // 进度条拖拽
  (function bindBar() {
    const bar = $('#bar');
    let dragging = false;
    const seek = (e) => {
      const rect = bar.getBoundingClientRect();
      const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
      const ratio = rect.width ? x / rect.width : 0;
      const d = audio.duration || (state.current && state.current.duration) || 0;
      if (!d) return;
      $('#barFill').style.width = (ratio * 100) + '%';
      $('#barDot').style.left = (ratio * 100) + '%';
      $('#tCur').textContent = fmt(ratio * d);
      return ratio * d;
    };
    bar.addEventListener('pointerdown', (e) => {
      if (!state.current) return;
      dragging = true;
      bar.classList.add('dragging');
      bar.setPointerCapture(e.pointerId);
      seek(e);
    });
    bar.addEventListener('pointermove', (e) => {
      if (dragging) seek(e);
    });
    bar.addEventListener('pointerup', (e) => {
      if (!dragging) return;
      dragging = false;
      bar.classList.remove('dragging');
      const t = seek(e);
      if (t != null) { try { audio.currentTime = t; } catch (_) {} }
    });
    bar.addEventListener('pointercancel', () => {
      dragging = false;
      bar.classList.remove('dragging');
    });
  })();

  // 音量
  (function bindVolume() {
    const vbar = $('#vbar'), vfill = $('#vfill');
    const apply = () => { vfill.style.width = (state.muted ? 0 : state.volume * 100) + '%'; };
    const setFromEvent = (e) => {
      const rect = vbar.getBoundingClientRect();
      const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
      state.volume = rect.width ? x / rect.width : 0.8;
      state.muted = state.volume === 0;
      audio.volume = state.volume;
      audio.muted = false;
      apply();
    };
    let dragging = false;
    vbar.addEventListener('pointerdown', (e) => {
      dragging = true; vbar.setPointerCapture(e.pointerId); setFromEvent(e);
    });
    vbar.addEventListener('pointermove', (e) => { if (dragging) setFromEvent(e); });
    vbar.addEventListener('pointerup', () => { dragging = false; saveVolume(); });
    $('#btnMute').addEventListener('click', () => {
      state.muted = !state.muted;
      audio.muted = state.muted;
      $('#btnMute').classList.toggle('on', state.muted);
      apply();
    });
    window.__applyVolume = () => { audio.volume = state.volume; apply(); };
  })();

  function saveVolume() {
    try { localStorage.setItem('ysj_vol', String(state.volume)); } catch (_) {}
  }

  // ---------------------------------------------------------------- 搜索
  async function doSearch() {
    const kw = ($('#q').value || '').trim();
    if (!kw) { toast('先写个曲名'); return; }
    // 切到云端页
    document.querySelectorAll('.nav-item').forEach(b =>
      b.classList.toggle('active', b.dataset.view === 'cloud'));
    state.view = 'cloud';
    state.cloud = [];
    renderList();
    loadingEl.hidden = false;
    emptyEl.hidden = true;
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(kw)}&limit=30`);
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || '搜索失败');
      state.cloud = d.songs.map(s => ({
        kind: 'cloud',
        id: s.id,
        source: s.source,
        title: s.title,
        artist: s.artist,
        album: s.album,
        pic_id: s.pic_id,
        duration: 0,
      }));
      if (!state.cloud.length) toast('云端未寻得此曲，换个写法试试');

      // 搜索接口不带时长，后台补一次
      if (state.cloud.length) {
        const ids = state.cloud.map(function (s) { return s.id; }).join(',');
        fetch('/api/durations?ids=' + encodeURIComponent(ids))
          .then(function (r) { return r.json(); })
          .then(function (dd) {
            if (!dd || !dd.ok || !dd.map) return;
            let any = false;
            state.cloud.forEach(function (s) {
              const v = dd.map[s.id];
              if (v && !s.duration) { s.duration = v; any = true; }
            });
            if (any && state.view === 'cloud') {
              Array.prototype.forEach.call(listEl.children, function (row, i) {
                const song = state.cloud[i];
                const du = row.querySelector('.du');
                if (song && du && song.duration) du.textContent = fmt(song.duration);
              });
            }
          })
          .catch(function () {});
      }
    } catch (e) {
      toast(e.message || '网络不通，云端暂不可达');
    } finally {
      loadingEl.hidden = true;
      renderList();
    }
  }

  $('#btnSearch').addEventListener('click', doSearch);
  $('#q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); doSearch(); }
    if (e.key === 'Escape') { $('#q').blur(); }
  });

  // ---------------------------------------------------------------- 歌词
  function parseLrc(text) {
    if (!text) return [];
    const lines = [];
    text.split(/\r?\n/).forEach(raw => {
      const m = raw.match(/\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g);
      if (!m) return;
      const content = raw.replace(/\[[^\]]*\]/g, '').trim();
      if (!content) return;
      m.forEach(tag => {
        const p = tag.slice(1, -1).split(/[.:]/);
        const t = parseInt(p[0], 10) * 60 + parseInt(p[1], 10) +
          (p[2] ? parseInt(p[2].padEnd(3, '0'), 10) / 1000 : 0);
        lines.push({ t, text: content });
      });
    });
    lines.sort((a, b) => a.t - b.t);
    return lines;
  }

  function renderLyric(lines, hint) {
    state.lyricLines = lines || [];
    const boxes = [$('#lyricBody'), $('#npLyric')].filter(Boolean);
    if (!state.lyricLines.length) {
      boxes.forEach(function (b) { b.innerHTML = '<p class="lyric-hint">' + (hint || '静听') + '</p>'; });
      return;
    }
    const html = state.lyricLines
      .map(function (l) { return '<p class="lyric-line">' + escapeHtml(l.text) + '</p>'; })
      .join('');
    boxes.forEach(function (b) { b.innerHTML = html; });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  let lastCur = -1;
  function syncLyric(t) {
    const lines = state.lyricLines;
    if (!lines.length) return;
    let idx = -1;
    for (let i = 0; i < lines.length; i++) {
      if (t >= lines[i].t) idx = i; else break;
    }
    if (idx === lastCur) return;
    lastCur = idx;
    ['#lyricBody', '#npLyric'].forEach(function (sel) {
      const box = $(sel);
      if (!box) return;
      const nodes = box.children;
      for (let i = 0; i < nodes.length; i++) nodes[i].classList.remove('cur');
      if (idx >= 0 && nodes[idx]) {
        nodes[idx].classList.add('cur');
        const el = nodes[idx];
        const target = el.offsetTop - box.clientHeight * 0.38 + el.offsetHeight / 2;
        box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
      }
    });
  }

  function normTitle(s) {
    return String(s || '').toLowerCase()
      .replace(/[\(（\[【].*?[\)）\]】]/g, '')
      .replace(/[\s\-_.,·'"!?！？、，。]/g, '');
  }

  // 带重试的取数：网络抖动时自动再试，避免"第一次没词"
  async function fetchJson(url, retries, options) {
    const n = retries === undefined ? 2 : retries;
    let lastErr = null;
    for (let i = 0; i <= n; i++) {
      try {
        const r = await fetch(url, Object.assign({ cache: 'no-store' }, options || {}));
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
      } catch (e) {
        lastErr = e;
        if (i < n) await new Promise(function (res) { setTimeout(res, 350 * (i + 1)); });
      }
    }
    throw lastErr || new Error('请求失败');
  }

  async function loadLyric(song) {
    lastCur = -1;
    const key = song.kind + ':' + song.id + ':' + song.title;
    if (state.lyricKey === key) return;
    state.lyricKey = key;
    renderLyric([], '静听');

    // 缓存
    const ck = 'ysj_lrc_' + key;
    try {
      const cached = localStorage.getItem(ck);
      if (cached) {
        const obj = JSON.parse(cached);
        renderLyric(parseLrc(obj.lrc), '此曲无词，静听');
        return;
      }
    } catch (_) {}

    try {
      let cloudSong = null;
      if (song.kind === 'cloud') {
        cloudSong = song;
      } else {
        // 本地曲：按曲名联网寻词，标题需高度吻合才采用
        const nt = normTitle(song.title);
        if (nt.length >= 2) {
          const d = await fetchJson(`/api/search?q=${encodeURIComponent(song.title)}&limit=8`);
          if (d.ok && d.songs && d.songs.length) {
            cloudSong = d.songs.find(s => {
              const cand = normTitle(s.title);
              if (!cand) return false;
              if (cand === nt) return true;
              if (cand.length >= 2 && nt.length >= 2 &&
                  (cand.includes(nt) || nt.includes(cand))) return true;
              // 去掉「的/之」等虚词后再比一次
              const strip = function (x) { return x.replace(/[的之]/g, ''); };
              const a = strip(cand), b = strip(nt);
              return a === b || (a.length >= 3 && b.length >= 3 && (a.includes(b) || b.includes(a)));
            }) || null;
          }
        }
      }
      if (!cloudSong) { renderLyric([], '此曲无词，静听'); return; }

      const d2 = await fetchJson(`/api/lyric?id=${encodeURIComponent(cloudSong.id)}&source=${encodeURIComponent(cloudSong.source || 'netease')}`);
      if (state.lyricKey !== key) return;
      const lrc = (d2 && d2.ok && d2.lyric) || '';
      const lines = parseLrc(lrc);
      renderLyric(lines, '此曲无词，静听');
      try { localStorage.setItem(ck, JSON.stringify({ lrc })); } catch (_) {}
    } catch (e) {
      if (state.lyricKey === key) renderLyric([], '词不可得');
    }
  }

  // 歌词卷开合
  // ---- 全屏词页（网易云式）----
  function isNowPlayingOpen() {
    return $('#nowPlaying').classList.contains('show');
  }
  function setNowPlaying(open) {
    const np = $('#nowPlaying');
    if (!np) return;
    if (open && !state.current) { toast('先放一首歌'); return; }
    np.classList.toggle('show', open);
    $('#btnLyric').classList.toggle('on', open);
    document.body.classList.toggle('np-open', open);
    if (open) {
      np.classList.toggle('playing', !audio.paused);
      lastCur = -2;                 // 强制刷新一次高亮
      renderLyric(state.lyricLines, '静听');
    }
  }
  function toggleNowPlaying() { setNowPlaying(!isNowPlayingOpen()); }

  $('#btnLyric').addEventListener('click', toggleNowPlaying);
  const npCloseBtn = $('#npClose');
  if (npCloseBtn) npCloseBtn.addEventListener('click', () => setNowPlaying(false));
  const npEl = $('#nowPlaying');
  if (npEl) {
    npEl.addEventListener('click', (e) => {
      if (e.target === npEl || e.target.classList.contains('np-veil')) setNowPlaying(false);
    });
  }
  const npDiscEl = $('#npDisc');
  if (npDiscEl) npDiscEl.addEventListener('click', (e) => { e.stopPropagation(); });

  // 点封面 = 进入全屏词页（仿网易云）
  $('#disc').addEventListener('click', () => {
    if (!state.current) { toast('先放一首歌'); return; }
    toggleNowPlaying();
  });

  // ---------------------------------------------------------------- 曲库
  let metaPoller = null;

  function sigOf(arr) {
    return arr.map(s => [s.id, s.artist || '', s.pic_id || '', s.album || ''].join('|')).join(',');
  }

  async function loadLocal(silent, force) {
    try {
      const r = await fetch('/api/local');
      const d = await r.json();
      state.favIds = new Set(d.favIds || []);
      const songs = (d.songs || []).map(s => ({ ...s, kind: s.kind || 'local' }));
      const changed = force || sigOf(songs) !== sigOf(state.local);
      $('#dirInfo').textContent = d.dir || '';

      if (!silent || changed) {
        const top = listEl.scrollTop;
        state.local = songs;
        $('#cntLike').textContent = songs.length;
        if (!state.queue.length) {
          state.queue = songs.slice();
        } else {
          state.queue = state.queue.map(q => {
            const f = songs.find(s => s.kind === q.kind && s.id === q.id);
            return f ? { ...q, ...f } : q;
          });
          // 已收藏的歌被移除后，从队列里清掉
          state.queue = state.queue.filter(q =>
            q.kind === 'local' || state.favIds.has(favKeyOf(q)));
        }
        if (state.view === 'like') {
          renderList();
          listEl.scrollTop = top;
        } else {
          updateHead();
        }
        // 当前曲补全了歌手/封面 → 刷新播放条
        if (state.current && state.current.kind === 'local') {
          const f = songs.find(s => s.id === state.current.id);
          if (f) {
            const need = (!state.current.artist && f.artist) || (!state.current.pic_id && f.pic_id);
            state.current = { ...state.current, ...f };
            if (need) { setMeta(state.current); setCover(state.current); }
          }
        }
      }

      if (d.metaBusy) {
        clearTimeout(metaPoller);
        metaPoller = setTimeout(() => loadLocal(true), 5000);
      }
    } catch (e) {
      if (!silent) toast('曲库读取失败');
    }
  }

  $('#btnRefresh').addEventListener('click', async () => {
    toast('重扫中⋯');
    try {
      const r = await fetch('/api/refresh');
      const d = await r.json();
      state.favIds = new Set(d.favIds || []);
      state.local = (d.songs || []).map(s => ({ ...s, kind: s.kind || 'local' }));
      state.queue = state.local.slice();
      renderList();
      toast(`曲库已更新，共 ${d.count} 曲`);
      setTimeout(() => loadLocal(true), 4000);
    } catch (_) { toast('重扫失败'); }
  });

  // ---------------------------------------------------------------- 收卷退出
  let quitArm = false;
  $('#btnQuit').addEventListener('click', async () => {
    const btn = $('#btnQuit');
    if (!quitArm) {
      quitArm = true;
      btn.textContent = '确定收起？';
      toast('再点一次，即收起曲卷退出播放器');
      setTimeout(() => { if (quitArm) { quitArm = false; btn.textContent = '收卷'; } }, 3200);
      return;
    }
    quitArm = false;
    audio.pause();
    try { await fetch('/api/quit'); } catch (_) {}
    document.body.innerHTML = '<div style="height:100vh;display:grid;place-items:center;'
      + "font-family:'KaiTi','STKaiti',serif;color:#8d8577;letter-spacing:.5em;font-size:15px;"
      + 'background:#f7f2e6">曲卷已收 · 可关闭此页</div>';
  });

  // ---------------------------------------------------------------- 沉浸模式
  function setImmersive(on) {
    document.body.classList.toggle('immersive', !!on);
    try { localStorage.setItem('ysj_immersive', on ? '1' : '0'); } catch (_) {}
  }
  const immerseBtn = document.getElementById('btnImmerse');
  if (immerseBtn) {
    immerseBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      setImmersive(true);
    });
  }
  // 双击侧栏「我的喜欢」= 收起界面只看壁纸
  const likeNav = document.querySelector('.nav-item[data-view="like"]');
  if (likeNav) {
    likeNav.setAttribute('title', '双击我：收起界面，只看壁纸');
    likeNav.addEventListener('dblclick', function (e) {
      e.preventDefault();
      e.stopPropagation();
      setImmersive(true);
    });
  }
  // 收起后点任意处唤回
  document.addEventListener('click', function () {
    if (document.body.classList.contains('immersive')) setImmersive(false);
  }, true);

  // 客户端化：屏蔽右键菜单与图片拖拽
  document.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  // 屏蔽浏览器专有快捷键，减少"网页感"
  document.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = (e.key || '').toLowerCase();
    if (['f', 'p', 's', 'u', 'j', 'd', 'g'].indexOf(k) >= 0) e.preventDefault();
  }, true);
  document.addEventListener('dragstart', function (e) { e.preventDefault(); });

  // ---------------------------------------------------------------- 键盘
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    switch (e.key) {
      case ' ':
        e.preventDefault();
        $('#btnPlay').click();
        break;
      case 'ArrowRight':
        e.preventDefault();
        audio.currentTime = Math.min(audio.currentTime + 5, audio.duration || 0);
        break;
      case 'ArrowLeft':
        e.preventDefault();
        audio.currentTime = Math.max(audio.currentTime - 5, 0);
        break;
      case 'ArrowUp':
        e.preventDefault();
        state.volume = Math.min(1, state.volume + 0.05);
        audio.volume = state.volume;
        window.__applyVolume && window.__applyVolume();
        saveVolume();
        break;
      case 'ArrowDown':
        e.preventDefault();
        state.volume = Math.max(0, state.volume - 0.05);
        audio.volume = state.volume;
        window.__applyVolume && window.__applyVolume();
        saveVolume();
        break;
      case 'n': case 'N': next(); break;
      case 'p': case 'P': prev(); break;
      case 'l': case 'L': $('#btnLyric').click(); break;
      case 'Escape':
        if (isNowPlayingOpen()) setNowPlaying(false);
        else if (document.body.classList.contains('immersive')) setImmersive(false);
        break;
    }
  });

  // ---------------------------------------------------------------- 启动
  (function boot() {
    let v = 0.8;
    try {
      const s = localStorage.getItem('ysj_vol');
      if (s != null) v = Math.max(0, Math.min(1, parseFloat(s)));
    } catch (_) {}
    state.volume = isFinite(v) ? v : 0.8;
    audio.volume = state.volume;
    window.__applyVolume && window.__applyVolume();

    syncModeIcon();
    loadLocal();
  })();

  // ---------------------------------------------------------------- 更换音乐目录
  (function dirPicker() {
    const modal = $('#dirModal');
    const input = $('#dirInput');
    const dirEl = $('#dirInfo');
    if (!modal || !dirEl) return;

    function open() {
      input.value = dirEl.textContent.trim();
      modal.hidden = false;
      setTimeout(function () { input.focus(); input.select(); }, 40);
    }
    function close() { modal.hidden = true; }

    dirEl.addEventListener('click', open);
    $('#dirCancel').addEventListener('click', close);
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });

    $('#dirOk').addEventListener('click', async function () {
      const p = (input.value || '').trim();
      if (!p) { toast('请填写目录路径'); return; }
      const btn = $('#dirOk');
      btn.disabled = true;
      btn.textContent = '扫描中⋯';
      try {
        const d = await fetchJson('/api/config', 0, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ musicDir: p }),
        });
        if (!d.ok) throw new Error(d.error || '设置失败');
        state.favIds = new Set(d.favIds || []);
        state.local = (d.songs || []).map(function (s) { return Object.assign({}, s, { kind: s.kind || 'local' }); });
        state.queue = state.local.slice();
        state.cloud = [];
        state.view = 'like';
        document.querySelectorAll('.nav-item').forEach(function (b) {
          b.classList.toggle('active', b.dataset.view === 'like');
        });
        dirEl.textContent = d.musicDir || p;
        renderList();
        updateHead();
        close();
        toast('已切换，共 ' + (d.count || 0) + ' 曲');
      } catch (e) {
        toast(e.message || '设置失败');
      } finally {
        btn.disabled = false;
        btn.textContent = '确定并重扫';
      }
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); $('#dirOk').click(); }
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
  })();

  // ---------------------------------------------------------------- 服务心跳
  let hbFail = 0;
  setInterval(function () {
    fetch('/api/ping', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw 0; hbFail = 0; document.body.classList.remove('offline'); })
      .catch(function () {
        hbFail += 1;
        if (hbFail >= 2) {
          document.body.classList.add('offline');
          document.title = '⚠ 服务已断开 — 云水间';
        }
      });
  }, 12000);

  // ---------------------------------------------------------------- 动态壁纸：飘雪
  (function snow() {
    const cv = document.getElementById('snow');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    let W = 0, H = 0, flakes = [];
    function resize() {
      W = cv.width = window.innerWidth;
      H = cv.height = window.innerHeight;
    }
    function seed() {
      flakes = [];
      const n = Math.max(50, Math.min(140, Math.round(W * H / 15000)));
      for (let i = 0; i < n; i++) {
        flakes.push({
          x: Math.random() * W,
          y: Math.random() * H,
          r: Math.random() * 2.2 + 1.0,
          vy: Math.random() * 0.34 + 0.10,
          vx: (Math.random() - 0.5) * 0.20,
          a: Math.random() * 0.45 + 0.28,
          ph: Math.random() * Math.PI * 2
        });
      }
    }
    resize(); seed();
    window.addEventListener('resize', function () { resize(); seed(); });
    let raf = 0;
    function tick(t) {
      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < flakes.length; i++) {
        const f = flakes[i];
        f.y += f.vy;
        f.x += f.vx + Math.sin(t / 3200 + f.ph) * 0.22;
        if (f.y > H + 4) { f.y = -4; f.x = Math.random() * W; }
        if (f.x > W + 4) f.x = -4;
        if (f.x < -4) f.x = W + 4;
        ctx.beginPath();
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(236,245,255,' + f.a + ')';
        ctx.fill();
      }
      raf = requestAnimationFrame(tick);
    }
    if (!window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      raf = requestAnimationFrame(tick);
    }
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { cancelAnimationFrame(raf); }
      else { raf = requestAnimationFrame(tick); }
    });
  })();

})();
