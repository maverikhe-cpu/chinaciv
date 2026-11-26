// Map-art extension: tile improvements (high-speed rail, conventional rail, expressways, the busiest airports and the
// principal ports) and wonders with protected land (国家公园), each as a static layer drawn into the cached map
// (nothing animates; an idle map draws no frames), with a switch in the "图层" menu.
//
// Data: infrastructure.json and wonders.json (loaded eagerly by main.js right after the first frame; app.loadData
// returns the same promise). A missing file (null) leaves its layers off and their switches harmless. Defaults: all
// on (roads and rail only from the region view in: see each layer's minRpx). Phase 1 has no military or trade layers.
import { createModel } from "./model.js";
import { makeLayers } from "./layers.js";
import { tips } from "./tips.js";

const TOGGLES = [
  { id: "rail", label: "铁路", icon: "t-rail", def: true, title: "高速铁路（亮色带刻度）与普速铁路（OpenStreetMap，作为地图装饰）", layers: ["rail"] },
  { id: "roads", label: "高速公路", icon: "t-road", def: true, title: "国家及省级高速公路（OpenStreetMap，作为地图装饰）", layers: ["roads"] },
  { id: "hubs", label: "港口与机场", icon: "d-harbor", def: true, title: "按货物吞吐量排名的主要港口（交通运输部）与按旅客吞吐量排名的繁忙机场（中国民用航空局）", layers: ["hubs"] },
  { id: "wonders", label: "奇观", icon: "w-landmark", def: true, title: "世界遗产、国家公园、5A 级景区与一流大学（按公开规则选取）", layers: ["wonders", "protected", "plates"] },
];
const FILES = ["infrastructure", "wonders"];

export function install(app) {
  const model = createModel(app.w);
  const on = Object.fromEntries(TOGGLES.map((t) => [t.id, t.def]));
  const layers = makeLayers(model, on, { tips, app });
  for (const l of layers) app.registerStaticLayer(l);

  for (const t of TOGGLES) {
    app.ui.addMapToggle({
      id: t.id, label: t.label, icon: t.icon, title: t.title, default: t.def,
      get: () => on[t.id],
      set: (v) => {
        on[t.id] = !!v;
        app.requestDraw();
      },
    });
  }

  const apply = (name, data) => {
    model.setData(name, data);
    app.requestDraw();
  };
  for (const name of FILES) {
    if (app.data[name] !== undefined) apply(name, app.data[name]);
    else app.loadData(name).then((d) => apply(name, d), (err) => console.warn(`map layers: ${name} failed to load`, err));
  }

  // For debugging and the gallery: the model and switches.
  app.mapArt = { model, on, layers };
}
