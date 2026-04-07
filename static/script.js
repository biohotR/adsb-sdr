// Initialize map centered on Bucharest
const map = L.map('map').setView([44.4268, 26.1025], 9);

// Add OpenStreetMap tiles
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
}).addTo(map);

// Store aircraft markers
const aircraftMarkers = {};
let selectedAircraft = null;
let isFirstLoad = true;
let selectedTrail = null;
const aircraftDetailCache = {};

const dataHealthEl = document.getElementById('data-health');

const setDataHealth = (state, text) => {
    dataHealthEl.classList.remove('ok', 'loading', 'error');
    dataHealthEl.classList.add(state);
    dataHealthEl.textContent = text;
};

// SVG airplane icon with altitude-based coloring
const createAircraftIcon = (heading, altitude) => {
    // Color based on altitude
    let color = '#e94560'; // default red
    if (altitude > 30000) color = '#2196F3'; // blue - high
    else if (altitude > 20000) color = '#4CAF50'; // green - cruise
    else if (altitude > 10000) color = '#FF9800'; // orange - climbing/descending
    // red for low altitude (landing/takeoff)
    
    return L.divIcon({
        html: `<svg width="32" height="32" viewBox="0 0 24 24" style="transform: rotate(${heading}deg); filter: drop-shadow(1px 1px 2px rgba(0,0,0,0.5));">
                <path fill="${color}" stroke="#fff" stroke-width="0.5" d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>
            </svg>`,
        className: 'aircraft-icon',
        iconSize: [32, 32],
        iconAnchor: [16, 16],
        popupAnchor: [0, -16]
    });
};

const formatAltitude = (alt) => alt ? `${Math.round(alt).toLocaleString()} ft` : 'N/A';
const formatSpeed = (spd) => spd ? `${Math.round(spd)} kts` : 'N/A';
const formatDistance = (km) => km ? `${km.toFixed(1)} km` : '0.0 km';
const formatDuration = (seconds) => {
    if (!seconds) return '0m';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
};
const formatVertRate = (rate) => {
    if (!rate || rate === 0) return 'Level';
    return rate > 0 ? `↑ ${rate} ft/min` : `↓ ${Math.abs(rate)} ft/min`;
};

const classifyAircraftFamily = (ac) => {
    const typeCode = (ac.t || ac.type || '').toUpperCase();
    if (typeCode.startsWith('B7') || typeCode.startsWith('A3')) return 'widebody';
    if (typeCode.startsWith('A3') || typeCode.startsWith('A2') || typeCode.startsWith('B3') || typeCode.startsWith('B73')) return 'narrowbody';
    if (typeCode.startsWith('E') || typeCode.startsWith('CRJ') || typeCode.startsWith('AT')) return 'regional';
    if (typeCode.startsWith('C') || typeCode.startsWith('LJ') || typeCode.startsWith('GLF') || typeCode.startsWith('F9')) return 'business';
    return 'generic';
};

const aircraftFamilyLabel = (family) => {
    const labels = {
        widebody: 'Wide-body Jet',
        narrowbody: 'Narrow-body Jet',
        regional: 'Regional Aircraft',
        business: 'Business Jet',
        generic: 'General Aircraft'
    };
    return labels[family] || labels.generic;
};

const renderAircraftProfile = (ac) => {
    const family = classifyAircraftFamily(ac);
    const labels = {
        widebody: { span: '60-65m', seats: '250-350' },
        narrowbody: { span: '34-36m', seats: '150-220' },
        regional: { span: '25-32m', seats: '50-110' },
        business: { span: '16-29m', seats: '6-19' },
        generic: { span: 'Unknown', seats: 'Unknown' }
    };

    return `
        <div class="visual-card">
            <h4>Aircraft Profile</h4>
            <svg viewBox="0 0 240 80" class="airframe-svg ${family}" aria-label="Aircraft profile illustration">
                <rect x="25" y="36" width="160" height="8" rx="4"></rect>
                <polygon points="10,40 28,34 28,46"></polygon>
                <polygon points="185,40 220,30 220,50"></polygon>
                <polygon points="85,40 130,18 150,18 115,40"></polygon>
                <polygon points="85,40 130,62 150,62 115,40"></polygon>
                <polygon points="38,40 52,24 62,24 52,40"></polygon>
            </svg>
            <div class="visual-grid">
                <div><span>Family</span><strong>${aircraftFamilyLabel(family)}</strong></div>
                <div><span>Type Code</span><strong>${ac.t || ac.type || 'N/A'}</strong></div>
                <div><span>Typical Span</span><strong>${labels[family].span}</strong></div>
                <div><span>Typical Seats</span><strong>${labels[family].seats}</strong></div>
            </div>
        </div>
    `;
};

const renderHobbyLinks = (links) => {
    if (!links) return '';
    return `
        <div class="detail-section">
            <h4>Community Links</h4>
            <div class="hobby-links">
                <a href="${links.adsbexchange}" target="_blank" rel="noopener noreferrer">ADSBExchange</a>
                <a href="${links.airframes}" target="_blank" rel="noopener noreferrer">Airframes</a>
            </div>
        </div>
    `;
};

const updateSelectedTrail = (historyPoints) => {
    if (selectedTrail) {
        map.removeLayer(selectedTrail);
        selectedTrail = null;
    }

    if (!historyPoints || historyPoints.length < 2) return;

    const latLngs = historyPoints
        .filter(point => point.lat && point.lon)
        .map(point => [point.lat, point.lon]);

    if (latLngs.length < 2) return;

    selectedTrail = L.polyline(latLngs, {
        color: '#4fc3f7',
        weight: 3,
        opacity: 0.8,
        dashArray: '6, 6'
    }).addTo(map);
};

const fetchAircraftDetails = async (hex) => {
    try {
        const response = await fetch(`/aircraft/${hex}/details`);
        if (!response.ok) return null;
        const details = await response.json();
        aircraftDetailCache[hex] = details;
        return details;
    } catch (error) {
        console.warn('Failed to load aircraft details', error);
        return null;
    }
};

// Create popup content
const createPopupContent = (ac) => `
    <div class="popup-content">
        <h3>✈️ ${ac.flight || 'Unknown'}</h3>
        <p class="airline-info">${ac.airline_name || ''} ${ac.origin ? `• ${ac.origin} → ${ac.destination}` : ''}</p>
        <hr style="border-color: #333; margin: 8px 0;">
        <div class="popup-grid">
            <p><span class="label">Aircraft:</span> ${ac.desc || ac.t || 'Unknown'}</p>
            <p><span class="label">Registration:</span> ${ac.r || 'N/A'}</p>
            <p><span class="label">Altitude:</span> ${formatAltitude(ac.alt_baro)}</p>
            <p><span class="label">Ground Speed:</span> ${formatSpeed(ac.gs)}</p>
            <p><span class="label">Vertical:</span> ${formatVertRate(ac.baro_rate)}</p>
            <p><span class="label">Heading:</span> ${Math.round(ac.track || 0)}°</p>
        </div>
        <button class="details-btn" onclick="showDetails('${ac.hex}')">View Full Details</button>
    </div>`;

const showDetails = (hex) => {
    selectedAircraft = hex;
    updateDetailPanel();
    document.getElementById('detail-panel').classList.add('visible');
    fetchAircraftDetails(hex).then(details => {
        if (!details || selectedAircraft !== hex) return;
        updateDetailPanel();
        updateSelectedTrail(details.history?.points || []);
    });
};

const hideDetails = () => {
    document.getElementById('detail-panel').classList.remove('visible');
    selectedAircraft = null;
    if (selectedTrail) {
        map.removeLayer(selectedTrail);
        selectedTrail = null;
    }
};

const updateDetailPanel = () => {
    if (!selectedAircraft) return;
    const ac = Object.values(aircraftMarkers).find(m => m.aircraftData?.hex === selectedAircraft)?.aircraftData;
    if (!ac) return;
    const details = aircraftDetailCache[selectedAircraft];
    const stats = details?.stats;
    const historyCount = details?.history?.count || 0;
    const metadata = details?.metadata || {};
    
    document.getElementById('detail-content').innerHTML = `
        <div class="detail-header">
            <h2>${ac.flight || 'Unknown'}</h2>
            <span class="detail-type">${ac.desc || ac.t || ''}</span>
        </div>

        ${renderAircraftProfile(ac)}
        
        <div class="detail-section">
            <h4>🛫 Flight Info</h4>
            <div class="detail-grid">
                <div class="detail-item"><span class="label">Airline</span><span class="value">${ac.airline_name || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Callsign</span><span class="value">${ac.flight || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Origin</span><span class="value">${ac.origin || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Destination</span><span class="value">${ac.destination || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Squawk</span><span class="value">${ac.squawk || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Emergency</span><span class="value ${ac.emergency !== 'none' ? 'emergency' : ''}">${ac.emergency || 'None'}</span></div>
            </div>
        </div>
        
        <div class="detail-section">
            <h4>✈️ Aircraft</h4>
            <div class="detail-grid">
                <div class="detail-item"><span class="label">Type</span><span class="value">${ac.t || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Description</span><span class="value">${ac.desc || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Registration</span><span class="value">${ac.r || metadata.registration || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Category</span><span class="value">${ac.category || metadata.category || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Hex (ICAO)</span><span class="value">${ac.hex || 'N/A'}</span></div>
            </div>
        </div>

        <div class="detail-section">
            <h4>🧭 Hobbyist Snapshot</h4>
            <div class="detail-grid">
                <div class="detail-item"><span class="label">First Seen</span><span class="value">${stats?.first_seen ? new Date(stats.first_seen * 1000).toLocaleTimeString() : 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Tracked For</span><span class="value">${formatDuration(stats?.tracked_duration_seconds)}</span></div>
                <div class="detail-item"><span class="label">Max Altitude</span><span class="value">${formatAltitude(stats?.max_altitude)}</span></div>
                <div class="detail-item"><span class="label">Max Speed</span><span class="value">${formatSpeed(stats?.max_speed)}</span></div>
                <div class="detail-item"><span class="label">Distance Tracked</span><span class="value">${formatDistance(stats?.total_distance_km)}</span></div>
                <div class="detail-item"><span class="label">History Points</span><span class="value">${historyCount}</span></div>
            </div>
        </div>

        ${renderHobbyLinks(details?.hobby_links)}
        
        <div class="detail-section">
            <h4>📍 Position & Movement</h4>
            <div class="detail-grid">
                <div class="detail-item"><span class="label">Latitude</span><span class="value">${ac.lat?.toFixed(5) || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Longitude</span><span class="value">${ac.lon?.toFixed(5) || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Baro Altitude</span><span class="value">${formatAltitude(ac.alt_baro)}</span></div>
                <div class="detail-item"><span class="label">Geo Altitude</span><span class="value">${formatAltitude(ac.alt_geom)}</span></div>
                <div class="detail-item"><span class="label">Ground Speed</span><span class="value">${formatSpeed(ac.gs)}</span></div>
                <div class="detail-item"><span class="label">IAS</span><span class="value">${formatSpeed(ac.ias)}</span></div>
                <div class="detail-item"><span class="label">Mach</span><span class="value">${ac.mach || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">Track</span><span class="value">${Math.round(ac.track || 0)}°</span></div>
                <div class="detail-item"><span class="label">Mag Heading</span><span class="value">${Math.round(ac.mag_heading || 0)}°</span></div>
                <div class="detail-item"><span class="label">Vertical Rate</span><span class="value">${formatVertRate(ac.baro_rate)}</span></div>
            </div>
        </div>
        
        <div class="detail-section">
            <h4>📡 Signal</h4>
            <div class="detail-grid">
                <div class="detail-item"><span class="label">Messages</span><span class="value">${ac.messages?.toLocaleString() || 'N/A'}</span></div>
                <div class="detail-item"><span class="label">RSSI</span><span class="value">${ac.rssi || 'N/A'} dBFS</span></div>
                <div class="detail-item"><span class="label">Last Seen</span><span class="value">${ac.seen?.toFixed(1) || '0'}s ago</span></div>
                <div class="detail-item"><span class="label">Nav QNH</span><span class="value">${ac.nav_qnh || 'N/A'} mb</span></div>
            </div>
        </div>

        <p class="trail-hint">Dashed blue line on map shows the recent tracked path for this aircraft.</p>
    `;
};

// Update aircraft table
const updateTable = (aircraft) => {
    const tbody = document.getElementById('aircraft-table-body');
    tbody.innerHTML = aircraft.filter(ac => ac.lat && ac.lon).map(ac => `
        <tr data-hex="${ac.hex}" class="${selectedAircraft === ac.hex ? 'selected' : ''}">
            <td><strong>${ac.flight || '---'}</strong><br><small class="subtext">${ac.t || ''}</small></td>
            <td>${Math.round(ac.alt_baro || 0).toLocaleString()}<br><small class="subtext">${formatVertRate(ac.baro_rate)}</small></td>
            <td>${Math.round(ac.gs || 0)}</td>
            <td>${Math.round(ac.track || 0)}°</td>
        </tr>`).join('');
    
    tbody.querySelectorAll('tr').forEach(row => {
        row.addEventListener('click', () => {
            const marker = aircraftMarkers[row.dataset.hex];
            if (marker) {
                map.setView(marker.getLatLng(), 11);
                marker.openPopup();
                showDetails(row.dataset.hex);
            }
        });
    });
};

// Fetch and update aircraft data
const updateAircraft = async () => {
    try {
        if (isFirstLoad) {
            setDataHealth('loading', 'Loading aircraft feed...');
        }

        const response = await fetch('/data');
        if (!response.ok) {
            throw new Error(`Data endpoint failed with status ${response.status}`);
        }

        const data = await response.json();
        const seenHex = new Set();
        
        data.aircraft.forEach(ac => {
            if (!ac.lat || !ac.lon) return;
            seenHex.add(ac.hex);
            
            if (aircraftMarkers[ac.hex]) {
                aircraftMarkers[ac.hex].setLatLng([ac.lat, ac.lon]);
                aircraftMarkers[ac.hex].setIcon(createAircraftIcon(ac.track || 0, ac.alt_baro || 0));
                aircraftMarkers[ac.hex].setPopupContent(createPopupContent(ac));
                aircraftMarkers[ac.hex].aircraftData = ac;
            } else {
                const marker = L.marker([ac.lat, ac.lon], {
                    icon: createAircraftIcon(ac.track || 0, ac.alt_baro || 0)
                }).addTo(map).bindPopup(createPopupContent(ac));
                marker.aircraftData = ac;
                marker.on('click', () => showDetails(ac.hex));
                aircraftMarkers[ac.hex] = marker;
            }
        });
        
        Object.keys(aircraftMarkers).forEach(hex => {
            if (!seenHex.has(hex)) {
                map.removeLayer(aircraftMarkers[hex]);
                delete aircraftMarkers[hex];
            }
        });
        
        document.getElementById('aircraft-count').textContent = `${seenHex.size} aircraft`;
        updateTable(data.aircraft);
        updateDetailPanel();
        setDataHealth('ok', `Live feed healthy · ${new Date().toLocaleTimeString()}`);
        isFirstLoad = false;
    } catch (e) {
        console.error('Error:', e);
        setDataHealth('error', 'Data feed unavailable. Retrying...');
    }
};

// Expose to global scope for popup button
window.showDetails = showDetails;
window.hideDetails = hideDetails;

// Check server status
fetch('/status').then(r => r.json()).then(data => {
    const badge = document.getElementById('mode-badge');
    badge.textContent = data.mode.toUpperCase();
    if (data.mode === 'live') badge.classList.add('live');

    if (!data.running) {
        setDataHealth('error', 'Receiver JSON not found. Check dump1090 path.');
        return;
    }

    if (data.metadata_error) {
        setDataHealth('loading', 'Receiver OK. Metadata API degraded.');
        return;
    }

    setDataHealth('loading', 'Receiver connected. Waiting for first update...');
}).catch(() => {
    setDataHealth('error', 'Status endpoint unreachable. Check backend server.');
});

// Initial load and start update interval
updateAircraft();
setInterval(updateAircraft, 1000);
