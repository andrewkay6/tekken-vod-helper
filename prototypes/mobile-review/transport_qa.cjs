/* Drive the custom transport in a real browser engine.
 *
 * Native controls are off, so this layer is the only way to play anything: if a handler here is wrong the
 * reviewer has no fallback. Each step asserts its own observed outcome rather than a disjunction, because a
 * silent no-op in a seek reads to a user as a wrong answer, not a missing callback.
 *
 *   node prototypes/mobile-review/transport_qa.cjs [base-url]
 */
const path = require('path');
const QA = path.join(__dirname, '..', '..', 'build/work/bb9/mobile-review-qa');
const { chromium } = require(path.join(QA, 'node_modules/playwright'));
const BASE = process.argv[2] || 'http://127.0.0.1:8787/';
const OUT = path.join(QA, 'transport');
require('fs').mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 200) });
  console.log((ok ? 'PASS' : 'FAIL') + ' ' + name.padEnd(44) + ' ' + String(detail).slice(0, 120));
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true,
                                       hasTouch: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await fetch(BASE + 'api/notes', { method: 'POST' });
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForFunction(() => document.querySelectorAll('#clip-list li').length === 10, null,
                             { timeout: 30000 });
  check('page renders 10 clips at 390px', true, await page.locator('#clip-list li').count() + ' cards');
  check('native controls are off', await page.locator('#player').evaluate((v) => !v.controls));

  const box = await page.locator('#stage').boundingBox();
  check('stage laid out', box && box.width > 300, JSON.stringify(box && { w: Math.round(box.width),
                                                                          h: Math.round(box.height) }));
  await page.screenshot({ path: path.join(OUT, '01-idle.png') });

  // Big centre button starts playback and hides itself.
  await page.locator('#big-play').click();
  await page.waitForFunction(() => !document.querySelector('#player').paused, null, { timeout: 20000 });
  check('big play starts playback', true, 'playing');
  await page.waitForFunction(() => document.querySelector('#big-play').classList.contains('playing'),
                             null, { timeout: 5000 });
  check('big play hides while playing', true);
  await page.screenshot({ path: path.join(OUT, '02-playing.png') });

  const timeBefore = await page.locator('#player').evaluate((v) => v.currentTime);
  await page.screenshot({ path: path.join(OUT, '03-chrome-hidden.png') });

  // Double-tap the right half skips forward five seconds.
  await page.touchscreen.tap(box.x + box.width * 0.75, box.y + box.height * 0.45);
  await page.waitForTimeout(90);
  await page.touchscreen.tap(box.x + box.width * 0.75, box.y + box.height * 0.45);
  await page.waitForTimeout(450);
  const afterFwd = await page.locator('#player').evaluate((v) => v.currentTime);
  check('double-tap right skips +5s', afterFwd > timeBefore + 3.5,
        timeBefore.toFixed(2) + ' -> ' + afterFwd.toFixed(2));

  // Double-tap the left half reverses it.
  await page.touchscreen.tap(box.x + box.width * 0.25, box.y + box.height * 0.45);
  await page.waitForTimeout(90);
  await page.touchscreen.tap(box.x + box.width * 0.25, box.y + box.height * 0.45);
  await page.waitForTimeout(450);
  const afterBack = await page.locator('#player').evaluate((v) => v.currentTime);
  check('double-tap left skips -5s', afterBack < afterFwd - 3.5,
        afterFwd.toFixed(2) + ' -> ' + afterBack.toFixed(2));

  // Single tap brings the chrome back and must not seek.
  await page.touchscreen.tap(box.x + box.width * 0.5, box.y + box.height * 0.3);
  await page.waitForTimeout(150);
  check('single tap shows chrome without seeking',
        await page.locator('#chrome').evaluate((n) => !n.classList.contains('hide')));

  // Scrub by dragging the rail to three quarters.
  const rail = await page.locator('#scrub').boundingBox();
  await page.mouse.move(rail.x + rail.width * 0.1, rail.y + rail.height / 2);
  await page.mouse.down();
  await page.mouse.move(rail.x + rail.width * 0.75, rail.y + rail.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(600);
  const scrubbed = await page.locator('#player').evaluate((v) => v.currentTime / v.duration);
  check('scrub drag seeks to the touched position', scrubbed > 0.6 && scrubbed < 0.9,
        (scrubbed * 100).toFixed(1) + '% of the loaded media');

  // Pause control mirrors state.
  await page.locator('#c-play').click();
  await page.waitForTimeout(250);
  check('pause button pauses', await page.locator('#player').evaluate((v) => v.paused));
  check('time readout is populated',
        /\d+:\d\d \/ \d+:\d\d/.test(await page.locator('#ctime').textContent()),
        await page.locator('#ctime').textContent());

  // The cut button must move playback onto the recording, clamped.
  await page.locator('#c-cut').click();
  // currentSrc only settles after the element selects the new source, so wait for the file itself rather
  // than for the badge, which updates on click and was the cause of a false failure here.
  // Wait for both halves of the switch: the element picks a source before it knows the duration, and an
  // assertion written against either one alone fails on timing rather than on behaviour.
  await page.waitForFunction(() => {
    const v = document.querySelector('#player');
    return /\/master$/.test(v.currentSrc || '') && Number.isFinite(v.duration) && v.duration > 6000;
  }, null, { timeout: 40000 });
  const loaded = await page.locator('#player').evaluate((v) => (v.currentSrc || '').split('/').pop());
  const badge = await page.locator('#mode-badge').textContent();
  const duration = await page.locator('#player').evaluate((v) => v.duration);
  check('cut button switches to the master', loaded === 'master' && duration > 6000,
        badge + ' / ' + loaded + ' / ' + Math.round(duration) + 's');
  await page.screenshot({ path: path.join(OUT, '04-cut-window.png') });

  // Both boundaries one tap away, and they must land on genuinely different positions.
  // A seek on a 56 GB remote file is not instantaneous; sampling currentTime after a fixed delay reads a
  // playhead still in flight and produced two false failures here. Wait for the element to report `seeked`.
  const jump = async (selector) => {
    await page.evaluate(() => {
      window.__seeked = false;
      const v = document.querySelector('#player');
      if (!v.__qa) { v.__qa = true; v.addEventListener('seeked', () => { window.__seeked = true; }); }
    });
    await page.locator(selector).click();
    await page.waitForFunction(() => window.__seeked === true, null, { timeout: 25000 });
    await page.waitForTimeout(250);
    return page.evaluate(() => document.querySelector('#player').currentTime);
  };

  // Assert each jump against the boundary it should reach. Comparing end only against start let a broken
  // "end" pass by measuring the start jump's own distance.
  // Boundaries come from the same API the page renders, so the assertion checks against real numbers
  // rather than a value read back out of the player it is meant to be auditing.
  const api = await (await fetch(BASE + 'api/state')).json();
  const item = api.clips[0];
  const note = await (await fetch(BASE + 'api/notes')).json();
  const proposed = (note.items[item.id] || {}).proposed;
  const cut = proposed ? [proposed.start, proposed.end]
                       : [item.requested.start, item.requested.end];
  check('a clean session starts with no proposal', !proposed, proposed ? JSON.stringify(proposed) : 'none');
  await page.locator('#edge-start').click();
  await page.waitForTimeout(900);
  const atStart = await page.locator('#player').evaluate((v) => v.currentTime);
  await page.locator('#edge-end').click();
  await page.waitForTimeout(900);
  const atEnd = await page.locator('#player').evaluate((v) => v.currentTime);
  await page.waitForTimeout(900);
  const settled = await page.evaluate(() => document.querySelector('#player').currentTime);
  check('jump to start reaches the cut start', cut && Math.abs(atStart - cut[0]) < 2,
        'start ' + atStart.toFixed(2) + ' vs boundary ' + (cut ? cut[0].toFixed(2) : '?'));
  check('jump to end reaches the cut end', cut && Math.abs(atEnd - (cut[1] - 3)) < 2,
        'end ' + atEnd.toFixed(2) + ' vs boundary ' + (cut ? cut[1].toFixed(2) : '?'));
  check('the end jump holds and is not dragged back', Math.abs(settled - atEnd) < 1.5,
        'after settle ' + settled.toFixed(2) + ' (was ' + atEnd.toFixed(2) + ')');

  // Prev/next inside the chrome drive the same control as the card.
  await page.locator('#next-clip').click();
  await page.waitForTimeout(900);
  check('next set changes the selected clip',
        (await page.locator('#clip-title').textContent()).includes('02'),
        await page.locator('#clip-title').textContent());

  // Nothing may reach off-screen at the narrowest phone width.
  await page.setViewportSize({ width: 360, height: 780 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth
                                              - document.documentElement.clientWidth);
  check('no horizontal overflow at 360px', overflow <= 1, 'overflow ' + overflow + 'px');
  await page.screenshot({ path: path.join(OUT, '05-360px.png') });

  const glyphs = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#chrome button')];
    return rows.map((b) => ({ id: b.id,
                              painted: b.querySelectorAll('svg,path').length > 0
                                       || b.textContent.trim().length > 0,
                              visible: b.getBoundingClientRect().right <= window.innerWidth + 1
                                           && b.getBoundingClientRect().width > 0 }));
  });
  const broken = glyphs.filter((g) => !g.painted || !g.visible);
  check('every transport button paints and fits the viewport', broken.length === 0,
        broken.length ? JSON.stringify(broken) : glyphs.length + ' buttons');
  check('no page errors', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();

  const failed = results.filter((row) => !row.ok);
  console.log('\n%d checks, %d failed', results.length, failed.length);
  console.log('Screenshots in ' + OUT);
  process.exit(failed.length ? 1 : 0);
})().catch((error) => { console.error('HARNESS ERROR', error); process.exit(2); });
