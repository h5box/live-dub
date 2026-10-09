<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import NavRail, { type View } from "./components/NavRail.vue";
import { load, save } from "./lib/prefs";
import { connect, disconnect } from "./stores/live";
import HistoryView from "./views/HistoryView.vue";
import LiveView from "./views/LiveView.vue";
import ServicesView from "./views/ServicesView.vue";
import SetupView from "./views/SetupView.vue";
import { live } from "./stores/live";

const VIEWS = { live: LiveView, history: HistoryView, services: ServicesView, setup: SetupView };
const view = ref<View>(load<View>("view", "live"));
if (!(view.value in VIEWS)) view.value = "live";
watch(view, (v) => save("view", v));
watch(() => live.doctor, (report) => {
  if (report && !report.ok && !load("setupDone", false)) view.value = "setup";
});

onMounted(connect);
onUnmounted(disconnect);
</script>

<template>
  <div class="app">
    <NavRail v-model="view" />
    <main class="main">
      <KeepAlive><component :is="VIEWS[view]" @navigate="view = $event" /></KeepAlive>
    </main>
  </div>
</template>

<style scoped>
.app { height: 100%; display: flex; }
.main { flex: 1; min-width: 0; height: 100%; }
</style>
