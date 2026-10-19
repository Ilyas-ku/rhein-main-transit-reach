"""Link-preview image (og:image, 1200x627) for the reach map: the live map
from Hauptwache at 08:00 with the title on a fade, captured with Playwright."""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
URL = "https://ilyas-ku.github.io/rhein-main-transit-reach/?lang=en#50.11410,8.67880,12,0,time"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    pg = b.new_page(viewport={"width": 1200, "height": 627}, device_scale_factor=1)
    pg.goto(URL)
    pg.wait_for_function("document.querySelectorAll('.leaflet-overlay-pane path').length > 1", timeout=60000)
    pg.evaluate("""(() => {
      map.setView([50.1141 - .02, 8.6788 - .1], 10.6, {animate: false});
      document.getElementById('panel').style.display = 'none';
      document.querySelector('.leaflet-control-zoom').style.display = 'none';
      const css = document.createElement('style');
      css.textContent = `#og { position: fixed; inset: 0; z-index: 7000; pointer-events: none; padding: 64px 0 0 64px;
          background: linear-gradient(to right, rgba(11,14,26,.97) 0%, rgba(11,14,26,.9) 34%, rgba(11,14,26,0) 58%); }
        #og .k { font: 600 17px 'Chakra Petch', sans-serif; letter-spacing: 5px; color: #8A90A8; }
        #og h1 { font: 700 54px/1.08 'Chakra Petch', sans-serif; color: #E6E8F0; margin: 16px 0 18px; }
        #og p { font: 400 22px/1.45 'IBM Plex Sans', sans-serif; color: #C9CDE0; margin: 0; }
        #og .tag { margin-top: 34px; display: inline-flex; gap: 10px; align-items: center; font: 500 17px 'JetBrains Mono', monospace; color: #8A90A8; }
        #og .tag i { display: inline-block; width: 24px; height: 11px; border-radius: 3px; }`;
      document.head.appendChild(css);
      const d = document.createElement('div'); d.id = 'og';
      d.innerHTML = '<div class="k">RHEIN-MAIN · PUBLIC TRANSPORT</div><h1>How far do you get<br>in an hour?</h1>' +
        '<p>An interactive map that counts real delays,<br>comparing them with the timetable</p>' +
        '<div class="tag"><i style="background:#26FFDB"></i><i style="background:#07B89C"></i><i style="background:#066E5E"></i>20 → 60 min from Hauptwache, 08:00</div>';
      document.body.appendChild(d);
    })()""")
    pg.wait_for_timeout(3500)
    out = HERE / "explore" / "preview.png"
    pg.screenshot(path=str(out))
    b.close()
    print("wrote", out)
