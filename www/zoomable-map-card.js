/**
 * Zoomable 3D-Vision Vector Map Card for Home Assistant Lovelace (v2.0.0)
 * - Fixed Header, Footer & Legend (never scroll or zoom off-screen)
 * - True SVG Vector Map rendering (razor-sharp at any zoom level, 0% pixelation)
 * - Smooth Mousewheel Zoom, Drag-to-Pan, Touch Pinch-to-Zoom, Double-click & Fullscreen
 * - Live Dynamic Scale Bar (meters -> screen pixels)
 */

const ZONE_PALETTE = [
  { fill: 'rgba(56, 189, 248, 0.28)', stroke: 'rgba(56, 189, 248, 0.92)', text: '#38bdf8' },   // Sky Cyan
  { fill: 'rgba(168, 85, 247, 0.28)', stroke: 'rgba(192, 132, 252, 0.92)', text: '#c084fc' },  // Purple
  { fill: 'rgba(245, 158, 11, 0.28)', stroke: 'rgba(251, 191, 36, 0.92)', text: '#fbbf24' },   // Amber
  { fill: 'rgba(16, 185, 129, 0.28)', stroke: 'rgba(52, 211, 153, 0.92)', text: '#34d399' },   // Emerald
  { fill: 'rgba(236, 72, 153, 0.28)', stroke: 'rgba(244, 114, 182, 0.92)', text: '#f472b6' },  // Pink
  { fill: 'rgba(99, 102, 241, 0.28)', stroke: 'rgba(129, 140, 248, 0.92)', text: '#818cf8' },  // Indigo
];

const ACTIVE_ZONE_STYLE = {
  fill: 'rgba(34, 197, 94, 0.45)',
  stroke: '#4ade80',
  text: '#4ade80',
};

class ZoomableMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;

    // Map data state
    this._mapData = null;
    this._lastFetchKey = '';
    this._fetching = false;

    // Pan & Zoom state (in screen pixels relative to fitted center)
    this._scale = 1.0;
    this._panX = 0;
    this._panY = 0;
    this._basePxPerMeter = 40;

    // Interaction state
    this._isDragging = false;
    this._dragStartX = 0;
    this._dragStartY = 0;
    this._initialDistance = 0;
    this._initialScale = 1.0;
    this._touchMidX = 0;
    this._touchMidY = 0;

    // Bound handlers
    this._onMouseDown = this._onMouseDown.bind(this);
    this._onMouseMove = this._onMouseMove.bind(this);
    this._onMouseUp = this._onMouseUp.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onTouchStart = this._onTouchStart.bind(this);
    this._onTouchMove = this._onTouchMove.bind(this);
    this._onTouchEnd = this._onTouchEnd.bind(this);
    this._onDblClick = this._onDblClick.bind(this);
    this._zoomIn = this._zoomIn.bind(this);
    this._zoomOut = this._zoomOut.bind(this);
    this._resetZoom = this._resetZoom.bind(this);
    this._toggleFullscreen = this._toggleFullscreen.bind(this);
    this._onFullscreenChange = this._onFullscreenChange.bind(this);
    this._onResize = this._onResize.bind(this);
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Please define an entity (e.g. camera.wellbeing_fenrir_mapdata)');
    }
    this._config = {
      min_zoom: 0.6,
      max_zoom: 12.0,
      zoom_step: 1.35,
      height: '460px',
      ...config,
    };
    this._renderSkeleton();
  }

  set hass(hass) {
    this._hass = hass;
    this._checkAndFetchMapData();
  }

  getCardSize() {
    return 7;
  }

  static getStubConfig() {
    return {
      type: 'custom:zoomable-map-card',
      entity: 'camera.wellbeing_fenrir_mapdata',
    };
  }

  _renderSkeleton() {
    if (!this.shadowRoot) return;

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          position: relative;
          box-sizing: border-box;
          user-select: none;
          -webkit-user-select: none;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
        }
        ha-card {
          display: flex;
          flex-direction: column;
          overflow: hidden;
          background: #0d1321;
          border-radius: var(--ha-card-border-radius, 16px);
          border: 1px solid rgba(51, 65, 85, 0.7);
          box-shadow: var(--ha-card-box-shadow, 0 6px 24px rgba(0, 0, 0, 0.35));
          margin: 0;
          padding: 0;
          color: #f8fafc;
        }
        ha-card:fullscreen, ha-card:-webkit-full-screen {
          width: 100vw;
          height: 100vh;
          border-radius: 0;
          border: none;
        }
        ha-card:fullscreen .viewport, ha-card:-webkit-full-screen .viewport {
          flex: 1;
          height: auto !important;
        }

        /* FIXED HEADER */
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
          padding: 12px 16px;
          background: #141d2f;
          border-bottom: 1px solid #334155;
          z-index: 20;
          flex-shrink: 0;
        }
        .header-left {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .robot-title {
          font-size: 16px;
          font-weight: 700;
          color: #f8fafc;
          letter-spacing: 0.2px;
        }
        .map-subtitle {
          font-size: 12.5px;
          color: #94a3b8;
        }
        .header-right {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 4px;
        }
        .status-pill {
          font-size: 12px;
          font-weight: 700;
          padding: 4px 11px;
          border-radius: 8px;
          border: 1.5px solid #38bdf8;
          background: #0f2c49;
          color: #38bdf8;
          white-space: nowrap;
        }
        .status-pill.cleaning {
          border-color: #4ade80;
          background: #0c3a2a;
          color: #4ade80;
        }
        .status-pill.warning {
          border-color: #fbbf24;
          background: #442c0c;
          color: #fbbf24;
        }
        .status-pill.error {
          border-color: #f87171;
          background: #45141a;
          color: #f87171;
        }
        .dustbin-label {
          font-size: 11.5px;
          font-weight: 500;
          color: #4ade80;
        }
        .dustbin-label.error {
          color: #f87171;
        }

        /* INTERACTIVE VECTOR VIEWPORT */
        .viewport {
          position: relative;
          width: 100%;
          height: ${this._config.height || '460px'};
          overflow: hidden;
          cursor: grab;
          touch-action: none;
          background-color: #0d1321;
          background-image:
            linear-gradient(to right, rgba(30, 41, 59, 0.45) 1px, transparent 1px),
            linear-gradient(to bottom, rgba(30, 41, 59, 0.45) 1px, transparent 1px);
          background-size: 36px 36px;
        }
        .viewport.dragging {
          cursor: grabbing;
        }
        svg.map-svg {
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          display: block;
          overflow: visible;
        }

        /* FLOATING CONTROLS */
        .controls-overlay {
          position: absolute;
          top: 12px;
          right: 12px;
          display: flex;
          flex-direction: column;
          gap: 6px;
          z-index: 15;
        }
        .ctrl-btn {
          width: 36px;
          height: 36px;
          border-radius: 10px;
          background: rgba(15, 23, 42, 0.86);
          backdrop-filter: blur(8px);
          -webkit-backdrop-filter: blur(8px);
          border: 1px solid rgba(255, 255, 255, 0.16);
          color: #f1f5f9;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 18px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
          outline: none;
          padding: 0;
        }
        .ctrl-btn:hover {
          background: rgba(30, 41, 59, 0.96);
          border-color: rgba(56, 189, 248, 0.65);
          color: #38bdf8;
        }
        .ctrl-btn:active {
          transform: scale(0.95);
        }
        .ctrl-btn svg {
          width: 18px;
          height: 18px;
          fill: currentColor;
        }

        /* DYNAMIC SCALE BAR & ZOOM BADGE (FIXED ON CANVAS) */
        .scale-overlay {
          position: absolute;
          bottom: 12px;
          left: 14px;
          display: flex;
          align-items: center;
          gap: 10px;
          background: rgba(13, 19, 33, 0.78);
          backdrop-filter: blur(6px);
          padding: 5px 10px;
          border-radius: 8px;
          border: 1px solid rgba(51, 65, 85, 0.6);
          pointer-events: none;
          z-index: 15;
        }
        .scale-bar-wrap {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 2px;
        }
        .scale-line {
          height: 6px;
          border-left: 2px solid #94a3b8;
          border-right: 2px solid #94a3b8;
          border-bottom: 2px solid #94a3b8;
          transition: width 0.08s ease-out;
        }
        .scale-text {
          font-size: 11px;
          color: #cbd5e1;
          font-weight: 600;
          white-space: nowrap;
        }
        .zoom-badge {
          position: absolute;
          bottom: 12px;
          right: 12px;
          background: rgba(15, 23, 42, 0.84);
          backdrop-filter: blur(6px);
          border: 1px solid rgba(255, 255, 255, 0.14);
          color: #94a3b8;
          font-size: 11.5px;
          font-weight: 600;
          padding: 4px 9px;
          border-radius: 7px;
          pointer-events: none;
          z-index: 15;
        }

        /* FIXED FOOTER */
        .footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
          padding: 10px 16px;
          background: #141d2f;
          border-top: 1px solid #334155;
          font-size: 12px;
          z-index: 20;
          flex-shrink: 0;
        }
        .footer-left {
          color: #94a3b8;
        }
        .footer-right {
          color: #38bdf8;
          font-weight: 500;
        }
        .loading-overlay {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          color: #94a3b8;
          font-size: 13px;
          pointer-events: none;
        }
      </style>

      <ha-card id="cardRoot">
        <!-- FIXED HEADER -->
        <div class="header" id="header">
          <div class="header-left">
            <div class="robot-title" id="robotTitle">Lade Karte...</div>
            <div class="map-subtitle" id="mapSubtitle">3D-Vision Vektorkarte</div>
          </div>
          <div class="header-right">
            <div class="status-pill" id="statusPill">—</div>
            <div class="dustbin-label" id="dustbinLabel">—</div>
          </div>
        </div>

        <!-- VECTOR VIEWPORT -->
        <div class="viewport" id="viewport">
          <svg class="map-svg" id="mapSvg" xmlns="http://www.w3.org/2000/svg">
            <g id="worldGroup"></g>
          </svg>

          <div class="loading-overlay" id="loadingOverlay">Lade Vektordaten...</div>

          <div class="controls-overlay">
            <button class="ctrl-btn" id="btnZoomIn" title="Heranzoomen (+)">+</button>
            <button class="ctrl-btn" id="btnZoomOut" title="Herauszoomen (−)">−</button>
            <button class="ctrl-btn" id="btnReset" title="Ansicht zurücksetzen (100%)">
              <svg viewBox="0 0 24 24"><path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46A7.93 7.93 0 0020 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74A7.93 7.93 0 004 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg>
            </button>
            <button class="ctrl-btn" id="btnFullscreen" title="Vollbild umschalten">
              <svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
            </button>
          </div>

          <div class="scale-overlay" id="scaleOverlay">
            <div class="scale-bar-wrap">
              <div class="scale-line" id="scaleLine" style="width: 60px;"></div>
            </div>
            <div class="scale-text" id="scaleText">2.0 m</div>
          </div>

          <div class="zoom-badge" id="zoomBadge">100%</div>
        </div>

        <!-- FIXED FOOTER / LEGEND -->
        <div class="footer" id="footer">
          <div class="footer-left" id="footerLeft">Verfügbare Karten: —</div>
          <div class="footer-right" id="footerRight">—</div>
        </div>
      </ha-card>
    `;

    const viewport = this.shadowRoot.getElementById('viewport');
    viewport.addEventListener('mousedown', this._onMouseDown);
    viewport.addEventListener('wheel', this._onWheel, { passive: false });
    viewport.addEventListener('touchstart', this._onTouchStart, { passive: false });
    viewport.addEventListener('touchmove', this._onTouchMove, { passive: false });
    viewport.addEventListener('touchend', this._onTouchEnd);
    viewport.addEventListener('touchcancel', this._onTouchEnd);
    viewport.addEventListener('dblclick', this._onDblClick);

    window.addEventListener('mousemove', this._onMouseMove);
    window.addEventListener('mouseup', this._onMouseUp);
    window.addEventListener('resize', this._onResize);

    this.shadowRoot.getElementById('btnZoomIn').addEventListener('click', this._zoomIn);
    this.shadowRoot.getElementById('btnZoomOut').addEventListener('click', this._zoomOut);
    this.shadowRoot.getElementById('btnReset').addEventListener('click', this._resetZoom);
    this.shadowRoot.getElementById('btnFullscreen').addEventListener('click', this._toggleFullscreen);

    document.addEventListener('fullscreenchange', this._onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', this._onFullscreenChange);

    if (this._hass) {
      this._checkAndFetchMapData();
    }
  }

  async _checkAndFetchMapData() {
    if (!this._hass || !this._config.entity || !this.shadowRoot) return;
    const stateObj = this._hass.states[this._config.entity];
    if (!stateObj) return;

    const attrs = stateObj.attributes || {};
    const pncId = attrs.pnc_id;
    if (!pncId) return;

    const fetchKey = `${pncId}:${attrs.active_map_id || ''}:${attrs.map_revision || 0}:${(attrs.active_cleaning_zones || []).join(',')}:${stateObj.last_updated}`;
    if (fetchKey === this._lastFetchKey || this._fetching) return;

    this._fetching = true;
    try {
      const data = await this._hass.callApi('GET', `wellbeing/map_data/${encodeURIComponent(pncId)}`);
      this._lastFetchKey = fetchKey;
      this._mapData = data;
      this._renderMapData();
    } catch (err) {
      console.error('ZoomableMapCard fetch error:', err);
      const loading = this.shadowRoot.getElementById('loadingOverlay');
      if (loading && !this._mapData) {
        loading.style.display = 'flex';
        loading.textContent = `Fehler beim Laden der Vektordaten (${err?.message || err?.status_code || 'API'})`;
      }
    } finally {
      this._fetching = false;
    }
  }

  _renderMapData() {
    const data = this._mapData;
    if (!data || !this.shadowRoot) return;

    const loading = this.shadowRoot.getElementById('loadingOverlay');
    if (loading) loading.style.display = 'none';

    const t = data.telemetry || {};

    // 1. Update Fixed Header
    const robotTitle = this.shadowRoot.getElementById('robotTitle');
    const mapSubtitle = this.shadowRoot.getElementById('mapSubtitle');
    const statusPill = this.shadowRoot.getElementById('statusPill');
    const dustbinLabel = this.shadowRoot.getElementById('dustbinLabel');

    if (robotTitle) robotTitle.textContent = `${t.name || 'Roboter'}  •  ${t.model || 'AEG'}`;
    if (mapSubtitle) {
      const scanText = t.crumb_cnt > 0
        ? `3D-Vision Scan (${t.crumb_cnt} Messpunkte)`
        : `${(t.all_maps || []).length} Karten in Cloud`;
      const sqmText = t.total_sqm > 0 ? `  •  Zonenfläche: ${t.total_sqm.toFixed(1)} m²` : '';
      mapSubtitle.textContent = `Aktive Karte: ${t.active_map || 'Keine'}  •  ${scanText}${sqmText}`;
    }
    if (statusPill) {
      statusPill.textContent = `${t.status_text}  |  Akku: ${t.bat_pct}%  |  Modus: ${t.fan_speed}`;
      statusPill.className = 'status-pill' + (t.is_cleaning ? ' cleaning' : t.is_error ? ' error' : t.is_warning ? ' warning' : '');
    }
    if (dustbinLabel) {
      dustbinLabel.textContent = `Staubbehälter: ${t.dustbin_text}`;
      dustbinLabel.className = 'dustbin-label' + (t.dustbin_ok ? '' : ' error');
    }

    // 2. Update Fixed Footer
    const footerLeft = this.shadowRoot.getElementById('footerLeft');
    const footerRight = this.shadowRoot.getElementById('footerRight');
    if (footerLeft) {
      const mapList = (t.all_maps || []).map(m => m === t.active_map ? `[${m}]` : m).join('  •  ');
      footerLeft.textContent = `Verfügbare Karten: ${mapList || 'Keine'}`;
    }
    if (footerRight) {
      const zNames = (data.zones || []).map(z => z.name);
      footerRight.textContent = zNames.length > 0
        ? `Räume (${zNames.length}): ${zNames.join(', ')}`
        : 'Modus: Komplette vermessene Etage';
    }

    // 3. Render SVG Vector World
    this._buildSvgWorld();
    this._applySvgTransform();
  }

  _buildSvgWorld() {
    const worldGroup = this.shadowRoot ? this.shadowRoot.getElementById('worldGroup') : null;
    if (!worldGroup || !this._mapData) return;

    const data = this._mapData;
    if (!data.bounds) {
      worldGroup.innerHTML = '';
      return;
    }

    const robotW = data.robot_width || 0.34;
    const wallW = robotW + 0.12;
    let svgHtml = '';

    // Layer 1A: Outer Wall / Obstacle Perimeter
    if (data.runs && data.runs.length > 0) {
      const dPaths = data.runs.map(run => {
        if (run.length === 1) {
          return `M ${run[0][0]} ${run[0][1]} l 0.001 0`;
        }
        return 'M ' + run.map(p => `${p[0]} ${p[1]}`).join(' L ');
      }).join(' ');

      svgHtml += `<path d="${dPaths}" fill="none" stroke="rgba(148, 163, 184, 0.94)" stroke-width="${wallW}" stroke-linecap="round" stroke-linejoin="round" />`;
      // Layer 1B: Scanned Room Floor Interior Swath
      svgHtml += `<path d="${dPaths}" fill="none" stroke="#2b4c7e" stroke-width="${robotW}" stroke-linecap="round" stroke-linejoin="round" />`;
      // Layer 1C: Driven Cleaning Trajectory Path
      svgHtml += `<path d="${dPaths}" fill="none" stroke="rgba(125, 211, 252, 0.58)" stroke-width="0.035" stroke-linecap="round" stroke-linejoin="round" />`;
    }

    // Layer 2: Interactive Zone Polygons
    (data.zones || []).forEach(z => {
      const style = z.active ? ACTIVE_ZONE_STYLE : ZONE_PALETTE[z.color_idx % ZONE_PALETTE.length];
      const ptsStr = z.vertices.map(v => `${v[0]},${v[1]}`).join(' ');
      svgHtml += `<polygon points="${ptsStr}" fill="${style.fill}" stroke="${style.stroke}" stroke-width="${z.active ? 2.5 : 1.8}" vector-effect="non-scaling-stroke" stroke-linejoin="round" />`;
      // Corner dots
      z.vertices.forEach(v => {
        svgHtml += `<circle cx="${v[0]}" cy="${v[1]}" r="0.065" fill="${style.stroke}" />`;
      });
    });

    // Layer 3: Charger Dock
    if (data.charger) {
      const [cx, cy] = data.charger;
      svgHtml += `
        <g class="charger-group" transform="translate(${cx}, ${cy})">
          <circle cx="0" cy="0" r="0.22" fill="#064e3b" stroke="#34d399" stroke-width="2" vector-effect="non-scaling-stroke" />
          <path d="M -0.02 -0.11 L -0.08 0.02 L -0.01 0.02 L -0.03 0.11 L 0.08 -0.02 L 0.01 -0.02 Z" fill="#34d399" />
          <g class="inv-scale" transform="translate(0, 0.34)">
            <rect x="-42" y="-10" width="84" height="20" rx="5" fill="rgba(6, 78, 59, 0.92)" stroke="rgba(52, 211, 153, 0.85)" stroke-width="1" />
            <text x="0" y="3.5" text-anchor="middle" fill="#d1fae5" font-size="11" font-weight="600">Ladestation</text>
          </g>
        </g>
      `;
    }

    // Layer 4: Room Labels (in inverse-scaled groups so text stays crisp & readable!)
    (data.zones || []).forEach(z => {
      const [cx, cy] = z.center;
      const style = z.active ? ACTIVE_ZONE_STYLE : ZONE_PALETTE[z.color_idx % ZONE_PALETTE.length];
      const subText = z.active ? 'REINIGT JETZT' : `${z.area_sqm.toFixed(1)} m²${z.power_mode ? ' • ' + z.power_mode : ''}`;
      const approxW = Math.max(z.name.length * 7.8 + 20, subText.length * 6.2 + 18, 90);
      const boxH = 38;

      svgHtml += `
        <g transform="translate(${cx}, ${cy})">
          <g class="inv-scale">
            <rect x="${-approxW / 2}" y="${-boxH / 2}" width="${approxW}" height="${boxH}" rx="7" fill="rgba(15, 23, 42, 0.90)" stroke="${style.stroke}" stroke-width="1.4" />
            <text x="0" y="-3" text-anchor="middle" fill="#f8fafc" font-size="12.5" font-weight="700">${this._escapeXml(z.name)}</text>
            <text x="0" y="12" text-anchor="middle" fill="${style.text}" font-size="10.5" font-weight="600">${this._escapeXml(subText)}</text>
          </g>
        </g>
      `;
    });

    worldGroup.innerHTML = svgHtml;
  }

  _escapeXml(str) {
    return String(str).replace(/[<>&"']/g, c => ({
      '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;',
    })[c]);
  }

  _applySvgTransform() {
    if (!this.shadowRoot || !this._mapData || !this._mapData.bounds) return;

    const viewport = this.shadowRoot.getElementById('viewport');
    const worldGroup = this.shadowRoot.getElementById('worldGroup');
    if (!viewport || !worldGroup) return;

    const rect = viewport.getBoundingClientRect();
    const vw = rect.width || 600;
    const vh = rect.height || 460;

    const b = this._mapData.bounds;
    const spanX = Math.max(b.max_x - b.min_x, 1.0);
    const spanY = Math.max(b.max_y - b.min_y, 1.0);
    const midX = (b.min_x + b.max_x) / 2;
    const midY = (b.min_y + b.max_y) / 2;

    const pad = 32;
    this._basePxPerMeter = Math.min((vw - 2 * pad) / spanX, (vh - 2 * pad) / spanY);
    const totalPxPerMeter = this._basePxPerMeter * this._scale;

    // Transform world meter coords -> viewport screen pixels
    const tx = vw / 2 + this._panX - midX * totalPxPerMeter;
    const ty = vh / 2 + this._panY - midY * totalPxPerMeter;

    worldGroup.setAttribute('transform', `translate(${tx}, ${ty}) scale(${totalPxPerMeter})`);

    // Keep text labels at crisp readable screen pixel size regardless of zoom!
    const invScaleFactor = 1.0 / totalPxPerMeter;
    const labelScale = Math.min(1.15, Math.max(0.85, Math.pow(this._scale, 0.25)));
    const finalLabelScale = invScaleFactor * labelScale;
    const invEls = worldGroup.querySelectorAll('.inv-scale');
    invEls.forEach(el => {
      const baseTransform = el.getAttribute('data-base') || '';
      if (!el.hasAttribute('data-base')) {
        const cur = el.getAttribute('transform') || '';
        el.setAttribute('data-base', cur);
      }
      const bT = el.getAttribute('data-base');
      el.setAttribute('transform', `${bT} scale(${finalLabelScale})`);
    });

    // Update dynamic Scale Bar
    this._updateScaleBar(totalPxPerMeter);

    // Update Zoom Badge
    const badge = this.shadowRoot.getElementById('zoomBadge');
    if (badge) {
      badge.textContent = `${Math.round(this._scale * 100)}%`;
    }
  }

  _updateScaleBar(pxPerMeter) {
    const scaleLine = this.shadowRoot ? this.shadowRoot.getElementById('scaleLine') : null;
    const scaleText = this.shadowRoot ? this.shadowRoot.getElementById('scaleText') : null;
    if (!scaleLine || !scaleText || !pxPerMeter) return;

    // Pick a clean meter step (5m, 2m, 1m, 0.5m, 0.2m) that fits nicely in 45..130 px
    const candidates = [5.0, 2.0, 1.0, 0.5, 0.2];
    let chosenM = 2.0;
    for (const m of candidates) {
      const px = m * pxPerMeter;
      if (px >= 45 && px <= 135) {
        chosenM = m;
        break;
      }
    }
    const barPx = Math.max(25, Math.min(180, chosenM * pxPerMeter));
    scaleLine.style.width = `${barPx.toFixed(1)}px`;
    scaleText.textContent = chosenM >= 1.0 ? `${chosenM.toFixed(1)} m` : `${Math.round(chosenM * 100)} cm`;
  }

  _onMouseDown(e) {
    if (e.target.closest('.controls-overlay')) return;
    this._isDragging = true;
    this._dragStartX = e.clientX - this._panX;
    this._dragStartY = e.clientY - this._panY;
    const viewport = this.shadowRoot.getElementById('viewport');
    if (viewport) viewport.classList.add('dragging');
  }

  _onMouseMove(e) {
    if (!this._isDragging) return;
    this._panX = e.clientX - this._dragStartX;
    this._panY = e.clientY - this._dragStartY;
    this._applySvgTransform();
  }

  _onMouseUp() {
    if (!this._isDragging) return;
    this._isDragging = false;
    const viewport = this.shadowRoot ? this.shadowRoot.getElementById('viewport') : null;
    if (viewport) viewport.classList.remove('dragging');
  }

  _onWheel(e) {
    e.preventDefault();
    const viewport = this.shadowRoot.getElementById('viewport');
    if (!viewport) return;

    const rect = viewport.getBoundingClientRect();
    const mouseX = e.clientX - rect.left - rect.width / 2;
    const mouseY = e.clientY - rect.top - rect.height / 2;

    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.87;
    const newScale = Math.max(
      this._config.min_zoom,
      Math.min(this._config.max_zoom, this._scale * zoomFactor)
    );

    if (newScale !== this._scale) {
      const scaleChange = newScale / this._scale;
      this._panX = mouseX - (mouseX - this._panX) * scaleChange;
      this._panY = mouseY - (mouseY - this._panY) * scaleChange;
      this._scale = newScale;
      this._applySvgTransform();
    }
  }

  _onTouchStart(e) {
    if (e.target.closest('.controls-overlay')) return;

    if (e.touches.length === 1) {
      this._isDragging = true;
      this._dragStartX = e.touches[0].clientX - this._panX;
      this._dragStartY = e.touches[0].clientY - this._panY;
    } else if (e.touches.length === 2) {
      this._isDragging = false;
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      this._initialDistance = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
      this._initialScale = this._scale;

      const viewport = this.shadowRoot.getElementById('viewport');
      const rect = viewport.getBoundingClientRect();
      this._touchMidX = (t1.clientX + t2.clientX) / 2 - rect.left - rect.width / 2;
      this._touchMidY = (t1.clientY + t2.clientY) / 2 - rect.top - rect.height / 2;
    }
  }

  _onTouchMove(e) {
    if (e.target.closest('.controls-overlay')) return;
    e.preventDefault();

    if (e.touches.length === 1 && this._isDragging) {
      this._panX = e.touches[0].clientX - this._dragStartX;
      this._panY = e.touches[0].clientY - this._dragStartY;
      this._applySvgTransform();
    } else if (e.touches.length === 2 && this._initialDistance > 0) {
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const dist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
      const factor = dist / this._initialDistance;
      const newScale = Math.max(
        this._config.min_zoom,
        Math.min(this._config.max_zoom, this._initialScale * factor)
      );

      const scaleChange = newScale / this._scale;
      this._panX = this._touchMidX - (this._touchMidX - this._panX) * scaleChange;
      this._panY = this._touchMidY - (this._touchMidY - this._panY) * scaleChange;
      this._scale = newScale;
      this._applySvgTransform();
    }
  }

  _onTouchEnd(e) {
    if (e.touches.length === 0) {
      this._isDragging = false;
      this._initialDistance = 0;
    } else if (e.touches.length === 1) {
      this._isDragging = true;
      this._dragStartX = e.touches[0].clientX - this._panX;
      this._dragStartY = e.touches[0].clientY - this._panY;
      this._initialDistance = 0;
    }
  }

  _onDblClick(e) {
    if (e.target.closest('.controls-overlay')) return;
    if (this._scale > 1.2) {
      this._resetZoom();
    } else {
      const viewport = this.shadowRoot.getElementById('viewport');
      const rect = viewport.getBoundingClientRect();
      const mouseX = e.clientX - rect.left - rect.width / 2;
      const mouseY = e.clientY - rect.top - rect.height / 2;

      const newScale = 2.4;
      const scaleChange = newScale / this._scale;
      this._panX = mouseX - (mouseX - this._panX) * scaleChange;
      this._panY = mouseY - (mouseY - this._panY) * scaleChange;
      this._scale = newScale;
      this._applySvgTransform();
    }
  }

  _zoomIn() {
    this._scale = Math.min(this._config.max_zoom, this._scale * (this._config.zoom_step || 1.35));
    this._applySvgTransform();
  }

  _zoomOut() {
    this._scale = Math.max(this._config.min_zoom, this._scale / (this._config.zoom_step || 1.35));
    if (this._scale <= 1.05 && this._scale >= 0.92) {
      this._scale = 1.0;
      this._panX = 0;
      this._panY = 0;
    }
    this._applySvgTransform();
  }

  _resetZoom() {
    this._scale = 1.0;
    this._panX = 0;
    this._panY = 0;
    this._applySvgTransform();
  }

  _toggleFullscreen() {
    const card = this.shadowRoot.getElementById('cardRoot');
    if (!card) return;

    if (!document.fullscreenElement && !document.webkitFullscreenElement) {
      if (card.requestFullscreen) {
        card.requestFullscreen();
      } else if (card.webkitRequestFullscreen) {
        card.webkitRequestFullscreen();
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen();
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      }
    }
  }

  _onFullscreenChange() {
    setTimeout(() => {
      this._resetZoom();
    }, 80);
  }

  _onResize() {
    this._applySvgTransform();
  }

  disconnectedCallback() {
    window.removeEventListener('mousemove', this._onMouseMove);
    window.removeEventListener('mouseup', this._onMouseUp);
    window.removeEventListener('resize', this._onResize);
    document.removeEventListener('fullscreenchange', this._onFullscreenChange);
    document.removeEventListener('webkitfullscreenchange', this._onFullscreenChange);
  }
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'zoomable-map-card',
  name: 'Zoomable 3D-Vision Vector Map Card',
  description: 'Razor-sharp SVG Vector Map with fixed header/legend and Pan & Zoom',
});

if (!customElements.get('zoomable-map-card')) {
  customElements.define('zoomable-map-card', ZoomableMapCard);
}

console.info(
  '%c ZOOMABLE-MAP-CARD %c v2.0.0 Vector SVG Loaded ',
  'color: white; background: #0284c7; font-weight: 700;',
  'color: #0284c7; background: #e0f2fe; font-weight: 700;'
);
