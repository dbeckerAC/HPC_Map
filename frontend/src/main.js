import maplibregl from "maplibre-gl";
import "./styles.css";

const TILESERVER_BASE = (import.meta.env.VITE_TILESERVER_BASE || "http://localhost:8080").replace(/\/$/, "");
const DISTANCE_TILES = `${TILESERVER_BASE}/data/hpc_distance/{z}/{x}/{y}.pbf`;
const HPC_TILES = `${TILESERVER_BASE}/data/hpc_sites/{z}/{x}/{y}.pbf`;
const hoverEl = document.getElementById("hover");
const hpcToggle = document.getElementById("hpc-toggle");

const map = new maplibregl.Map({
  container: "map",
  center: [10.5, 51.1],
  zoom: 5.6,
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "&copy; OpenStreetMap contributors"
      }
    },
    layers: [
      {
        id: "osm",
        type: "raster",
        source: "osm",
        paint: {
          "raster-opacity": 0.62,
          "raster-saturation": -0.75,
          "raster-contrast": 0.15,
          "raster-brightness-max": 0.78
        }
      }
    ]
  }
});

function addDistanceLayer(source, sourceLayer = null) {
  const casing = {
    id: "hpc-distance-casing",
    type: "line",
    source,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2.6, 12, 6.5],
      "line-color": "#0f141a",
      "line-opacity": 0.55
    }
  };
  if (sourceLayer) {
    casing["source-layer"] = sourceLayer;
  }
  map.addLayer(casing);

  const layer = {
    id: "hpc-distance",
    type: "line",
    source,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.8, 12, 4.6],
      "line-color": [
        "interpolate",
        ["linear"],
        ["coalesce", ["get", "distance_start_km"], 20],
        0,
        "#16a34a",
        8,
        "#facc15",
        14,
        "#f59e0b",
        20,
        "#dc2626"
      ],
      "line-opacity": 0.96
    }
  };
  if (sourceLayer) {
    layer["source-layer"] = sourceLayer;
  }
  map.addLayer(layer);
}

function addHpcLayer(source, sourceLayer = null) {
  const points = {
    id: "hpc-sites",
    type: "circle",
    source,
    minzoom: 5.5,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 1.6, 10, 2.8, 13, 4.4],
      "circle-color": "#1155cc",
      "circle-opacity": 0.84,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 0.7
    }
  };
  if (sourceLayer) {
    points["source-layer"] = sourceLayer;
  }
  map.addLayer(points);
}

map.on("load", () => {
  map.addSource("distance", { type: "vector", tiles: [DISTANCE_TILES], minzoom: 4, maxzoom: 22 });
  map.addSource("hpc", { type: "vector", tiles: [HPC_TILES], minzoom: 4, maxzoom: 22 });
  addDistanceLayer("distance", "hpc_distance");
  addHpcLayer("hpc", "hpc_sites");
  hoverEl.textContent = "Hover a motorway segment";
  bindHoverEvents();
});

function bindHoverEvents() {
  map.on("mousemove", "hpc-distance", (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const start = Number(feature.properties?.distance_start_km ?? NaN);
    const end = Number(feature.properties?.distance_end_km ?? NaN);
    const minPower = feature.properties?.min_power_kw ?? "n/a";
    const avg = Number.isFinite(start) && Number.isFinite(end) ? ((start + end) / 2).toFixed(2) : "n/a";
    hoverEl.textContent = `Distance: ${avg} km | threshold: ${minPower} kW`;
  });

  map.on("mouseleave", "hpc-distance", () => {
    hoverEl.textContent = "Hover a motorway segment";
  });

  map.on("mouseenter", "hpc-sites", () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mousemove", "hpc-sites", (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const chargerId = feature.properties?.charger_id ?? "n/a";
    const power = feature.properties?.power_kw ?? "n/a";
    const operator = feature.properties?.operator || "n/a";
    const status = feature.properties?.status || "n/a";
    hoverEl.textContent = `HPC ${chargerId} | ${power} kW | ${operator} | ${status}`;
  });

  map.on("mouseleave", "hpc-sites", () => {
    map.getCanvas().style.cursor = "";
    hoverEl.textContent = "Hover a motorway segment";
  });

  if (hpcToggle) {
    hpcToggle.addEventListener("change", () => {
      const visibility = hpcToggle.checked ? "visible" : "none";
      for (const layerId of ["hpc-sites"]) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, "visibility", visibility);
        }
      }
    });
  }
}
