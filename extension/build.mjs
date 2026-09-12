// 빌드 스크립트 (docs/05-구현가이드.md Phase 4, step 16; docs/02 ADR-13).
//
// 프레임워크 없이 esbuild만으로 번들한다 — React를 넣으면 유튜브 페이지에
// 주입할 때 충돌 위험이 커진다(ADR-13). 산출물은 extension/dist/에 모여
// 크롬 "압축해제된 확장 프로그램 로드"로 그대로 로드된다.

import { cpSync, mkdirSync, rmSync } from "node:fs";
import * as esbuild from "esbuild";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/popup", { recursive: true });
mkdirSync("dist/sidepanel", { recursive: true });

await esbuild.build({
  entryPoints: {
    background: "src/background.ts",
    "content/inject": "src/content/inject.ts",
    "popup/popup": "src/popup/popup.ts",
    "sidepanel/sidepanel": "src/sidepanel/sidepanel.ts",
  },
  outdir: "dist",
  bundle: true,
  format: "iife",
  target: "chrome110",
  sourcemap: true,
  logLevel: "info",
});

cpSync("manifest.json", "dist/manifest.json");
cpSync("src/popup/popup.html", "dist/popup/popup.html");
cpSync("src/sidepanel/sidepanel.html", "dist/sidepanel/sidepanel.html");

console.log("빌드 완료 → extension/dist (chrome://extensions → 압축해제된 확장 프로그램 로드)");
