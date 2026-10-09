// The services page's fixed bits per service; what each one is and where it runs comes from the backend
// (GET /api/services), because that depends on the machine's settings.
import type { SvcName } from "../api/types";

export const ORDER: SvcName[] = ["asr", "mt", "dub", "mediamtx"];

export const ICON: Record<SvcName, string> = { asr: "mic", mt: "translate", dub: "voice", mediamtx: "stream" };

export const STATE_LABEL = { up: "运行中", down: "未启动", starting: "启动中", stopping: "停止中" } as const;
