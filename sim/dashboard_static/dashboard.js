// Live mission dashboard client.
//
// Two independent SSE feeds, deliberately decoupled:
//   /stream            one combined telem+GPS row per FSW sample (~5s
//                       cadence, exactly as the FSW wrote it) -- drives the
//                       map, altitude/vspeed chart, and telemetry gauges.
//   /attitude_stream    20 Hz ground-truth IMU probe straight from the sim
//                       world -- drives *only* the 3D attitude cube, so it
//                       animates smoothly instead of snapping once every
//                       telemetry sample and sitting still in between.

let map, track, marker, followEl;
let mapHasFix = false;
let chart;
let scene, camera, renderer, cubeMesh, targetQuat = new THREE.Quaternion();
let lastPoint = null; // { t, alt } for vertical-speed finite difference

function initMap() {
  map = L.map("map", { zoomControl: true }).setView([42.2808, -83.7430], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  track = L.polyline([], { color: "#4fd1c5", weight: 3 }).addTo(map);
  marker = L.circleMarker([42.2808, -83.7430], { radius: 6, color: "#f0b429" }).addTo(map);

  // Leaflet reads its container's pixel size at creation time; if that
  // happens before the CSS grid layout has settled (very easy in a
  // grid/flex page like this one), the map ends up with a stale or zero
  // size and looks broken (grey, offset tiles, wrong drag bounds) until
  // something forces a resize. Nudge it once layout has actually
  // happened, and again on window resize.
  requestAnimationFrame(() => map.invalidateSize());
  setTimeout(() => map.invalidateSize(), 300);
  window.addEventListener("resize", () => map.invalidateSize());

  followEl = document.getElementById("follow-toggle");
  document.getElementById("btn-fit").addEventListener("click", () => {
    if (track.getLatLngs().length > 0) map.fitBounds(track.getBounds(), { padding: [24, 24] });
  });
}

function initChart() {
  const ctx = document.getElementById("altChart").getContext("2d");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Altitude (m)",
          data: [],
          borderColor: "#4fd1c5",
          yAxisID: "y",
          pointRadius: 0,
          tension: 0.15,
        },
        {
          label: "Vertical speed (m/s)",
          data: [],
          borderColor: "#f0b429",
          yAxisID: "y1",
          pointRadius: 0,
          tension: 0.15,
        },
      ],
    },
    options: {
      animation: false,
      parsing: false,
      scales: {
        x: { type: "linear", ticks: { color: "#7c8aa8" }, title: { display: true, text: "mission t (s)", color: "#7c8aa8" } },
        y: { position: "left", ticks: { color: "#4fd1c5" } },
        y1: { position: "right", ticks: { color: "#f0b429" }, grid: { drawOnChartArea: false } },
      },
      plugins: { legend: { labels: { color: "#d6e0f0" } } },
    },
  });
}

function initCube() {
  const el = document.getElementById("cube");
  const w = el.clientWidth, h = el.clientHeight;

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
  camera.position.set(2.2, 1.6, 2.6);
  camera.lookAt(0, 0, 0);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(w, h);
  el.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.8);
  dir.position.set(3, 4, 5);
  scene.add(dir);

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(1.2, 0.8, 1.6),
    new THREE.MeshStandardMaterial({ color: 0x2a3a55, metalness: 0.3, roughness: 0.6 })
  );
  const panel = new THREE.MeshStandardMaterial({ color: 0x1e2a3f, metalness: 0.1, roughness: 0.8 });
  const antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 1.4), panel);
  antenna.rotation.z = Math.PI / 2;
  antenna.position.set(0.9, 0, 0);

  cubeMesh = new THREE.Group();
  cubeMesh.add(body);
  cubeMesh.add(antenna);

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(body.geometry),
    new THREE.LineBasicMaterial({ color: 0x4fd1c5 })
  );
  cubeMesh.add(edges);

  scene.add(cubeMesh);
  animateCube();
}

function animateCube() {
  requestAnimationFrame(animateCube);
  // With a 20 Hz ground-truth feed there's a fresh target almost every
  // frame, so a gentle slerp is enough to look fluid without lagging
  // noticeably behind the real signal.
  if (cubeMesh) cubeMesh.quaternion.slerp(targetQuat, 0.35);
  renderer.render(scene, camera);
}

function eulerDegToQuat(rollDeg, pitchDeg, yawDeg) {
  const e = new THREE.Euler(
    THREE.MathUtils.degToRad(pitchDeg),
    THREE.MathUtils.degToRad(yawDeg),
    THREE.MathUtils.degToRad(rollDeg),
    "YXZ"
  );
  return new THREE.Quaternion().setFromEuler(e);
}

function fmt(v, digits = 2, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return v.toFixed(digits) + suffix;
}

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

function handleRow(row) {
  // -- map --
  if (row.has_fix && row.lat && row.lon) {
    const latlng = [row.lat, row.lon];
    track.addLatLng(latlng);
    marker.setLatLng(latlng);
    if (!mapHasFix) {
      // First fix of the mission: snap in and zoom to it.
      map.setView(latlng, 12);
      mapHasFix = true;
    } else if (followEl.checked) {
      // Every fix after that: keep the live position on-screen. Without
      // this the view was only ever set once, so as soon as the balloon
      // drifted more than half a screen away from the launch site the
      // marker silently moved off the visible map -- that's the "ground
      // tracker looks stuck/broken" symptom.
      map.panTo(latlng, { animate: true, duration: 0.5 });
    }
  }

  // -- vertical speed (finite difference between consecutive rows) --
  let vspeed = 0;
  if (lastPoint && row.unix_time > lastPoint.t) {
    vspeed = (row.alt_m - lastPoint.alt) / (row.unix_time - lastPoint.t);
  }
  lastPoint = { t: row.unix_time, alt: row.alt_m };

  if (chart.data.datasets[0].data.length === 0) {
    chart._t0 = row.unix_time;
  }
  const missionT = row.unix_time - (chart._t0 ?? row.unix_time);
  chart.data.datasets[0].data.push({ x: missionT, y: row.alt_m });
  chart.data.datasets[1].data.push({ x: missionT, y: vspeed });
  if (chart.data.datasets[0].data.length > 500) {
    chart.data.datasets[0].data.shift();
    chart.data.datasets[1].data.shift();
  }
  chart.update("none");

  // -- gauges --
  setText("g-time", fmt(missionT, 0, " s"));
  setText("g-pkt", row.packet_count ?? "--");
  setText("g-boot", row.boot_count ?? "--");
  setText("g-alt", fmt(row.alt_m, 0, ` m (${row.alt_source})`));
  setText("g-speed", row.speed_mps != null ? fmt(row.speed_mps, 1, " m/s") : "--");
  setText("g-sats", row.sats ?? "--");
  setText("g-batt", fmt(row.vbatt_v, 2, " V"));
  setText("g-ibatt", fmt(row.ibatt_a, 2, " A"));
  setText("g-bmetemp", fmt(row.bme_temp_c, 1, " °C"));
  setText("g-bmepress", fmt(row.bme_pressure_hpa, 1, " hPa"));
  setText("g-hum", fmt(row.bme_humidity_pct, 1, " %"));
  setText("g-cpu", `${fmt(row.cpu_temp_c, 1)}°C / ${fmt(row.cpu_load_pct, 0)}%`);
}

function handleAttitude(sample) {
  const { roll, pitch, yaw } = sample.attitude_deg;
  targetQuat = eulerDegToQuat(roll, pitch, yaw);
  setText("v-roll", fmt(roll, 1));
  setText("v-pitch", fmt(pitch, 1));
  setText("v-yaw", fmt(yaw, 1));
}

function connect() {
  const es = new EventSource("/stream");
  const pill = document.getElementById("conn-status");
  es.onopen = () => { pill.textContent = "live"; pill.className = "pill connected"; };
  es.onerror = () => { pill.textContent = "reconnecting…"; pill.className = "pill disconnected"; };
  es.onmessage = (ev) => {
    if (!ev.data) return;
    try {
      handleRow(JSON.parse(ev.data));
    } catch (e) {
      console.error("bad row", e, ev.data);
    }
  };
}

function connectAttitude() {
  const es = new EventSource("/attitude_stream");
  es.onmessage = (ev) => {
    if (!ev.data) return;
    try {
      handleAttitude(JSON.parse(ev.data));
    } catch (e) {
      console.error("bad attitude sample", e, ev.data);
    }
  };
  // Independent of the main /stream connection: if this one drops (e.g.
  // the dashboard was started without a matching mission clock) the rest
  // of the dashboard should keep working, just with a frozen cube.
  es.onerror = () => {};
}

window.addEventListener("DOMContentLoaded", () => {
  initMap();
  initChart();
  initCube();
  connect();
  connectAttitude();
});
