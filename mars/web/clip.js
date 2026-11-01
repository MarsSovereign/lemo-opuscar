// Frame-exact video clips inside a film page (pair with `python mars/media.py frames`).
//
//   import { loadClip } from '/mars/web/clip.js';
//   const host = await loadClip('clips/h3_s04/');          // folder relative to the film page (or an absolute /@film/… URL)
//   window.render = t => {
//     host.draw(g, t - 12.0, 0, 0, 1920, 1080);            // clip time = film time minus where the clip starts
//   };
//   window.READY = true;                                   // only after every loadClip has resolved
//
// Every still is decoded before READY, so any frame renders on its own, in any order, in any worker (no <video> seeking).
// Clip time outside [0, dur) holds the first / last frame unless { hold: false }, then draw() draws nothing.

export async function loadClip(dir, { hold = true } = {}) {
  const base = dir.endsWith('/') ? dir : dir + '/';
  const meta = await (await fetch(base + 'clip.json')).json();
  if (!meta.frames) throw new Error(`clip ${base}: clip.json lists no frames`);
  const imgs = await Promise.all([...Array(meta.frames)].map(async (_, i) => {
    const img = new Image();
    img.src = `${base}f${String(i + 1).padStart(5, '0')}.jpg`;
    await img.decode();                                    // throws (page error) if a still is missing or broken
    return img;
  }));
  const index = ct => {
    const i = Math.floor(ct * meta.fps + 1e-6);
    if (i < 0 || i >= meta.frames) return hold ? Math.min(Math.max(i, 0), meta.frames - 1) : -1;
    return i;
  };
  return {
    meta, dur: meta.dur, w: meta.w, h: meta.h,
    frame: ct => { const i = index(ct); return i < 0 ? null : imgs[i]; },
    draw(g, ct, x, y, w = meta.w, h = meta.h) { const im = this.frame(ct); if (im) g.drawImage(im, x, y, w, h); return !!im; },
  };
}
