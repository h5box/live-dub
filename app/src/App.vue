<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import Banner from "./components/Banner.vue";
import NavRail, { type View } from "./components/NavRail.vue";
import { load, save } from "./lib/prefs";
import { APP_VERSION, connect, disconnect, staleBackend } from "./stores/live";
import HistoryView from "./views/HistoryView.vue";
import LiveView from "./views/LiveView.vue";
import ServicesView from "./views/ServicesView.vue";
import SetupView from "./views/SetupView.vue";
import { live } from "./stores/live";

const VIEWS = { live: LiveView, history: HistoryView, services: ServicesView, setup: SetupView };
const view = ref<View>(load<View>("view", "live"));
if (!(view.value in VIEWS)) view.value = "live";
watch(view, (v) => save("view", v));
// an unfinished setup opens on the wizard once, at start; later checks (the environment page runs one when it
// opens) must not pull the user back there
const stopFirstCheck = watch(() => live.doctor, (report) => {
  if (!report) return;
  stopFirstCheck();
  if (!report.ok && !load("setupDone", false)) view.value = "setup";
});

onMounted(connect);
onUnmounted(disconnect);
</script>

<template>
  <div class="app">
    <NavRail v-model="view" />
    <main class="main">
      <!-- the window was updated but the backend from before is still running: what it lacks fails with odd errors -->
      <Banner v-if="staleBackend !== null" tone="warning" class="stale">
        正在运行的后端是{{ staleBackend ? ` ${staleBackend} 版` : "旧版本" }}，与当前界面（{{ APP_VERSION }}）不一致，部分功能无法使用。请重启后端：安装版在任务管理器中结束所有
        LiveDubBackend.exe 进程，再重新打开声影同传；从源码运行时，结束并重新运行 <code>python -m livedub.server</code>。
      </Banner>
      <div class="page"><KeepAlive><component :is="VIEWS[view]" @navigate="view = $event" /></KeepAlive></div>
    </main>
  </div>
</template>

<style scoped>
.app { height: 100%; display: flex; }
.main { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; }
.stale { margin: 12px 24px 0; flex: none; }
.page { flex: 1; min-height: 0; }
</style>
