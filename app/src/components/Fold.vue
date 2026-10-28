<script setup lang="ts">
// A card that folds to its header: the icon and title on the left, a summary on the right (where the part runs and
// its state), the body below when open. The whole header is one disclosure button.
import Icon from "./Icon.vue";

defineProps<{ id: string; icon: string; title: string; sub?: string }>();
const open = defineModel<boolean>("open", { default: false });
</script>

<template>
  <section class="fold card" :class="{ open }">
    <h2 class="fh">
      <button :id="`${id}-h`" class="fbtn" :aria-expanded="open" :aria-controls="`${id}-b`" @click="open = !open">
        <span class="si"><Icon :name="icon" :size="16" /></span>
        <span class="tt">{{ title }}</span>
        <span v-if="sub" class="sub">{{ sub }}</span>
        <span class="aside"><slot name="aside" /></span>
        <Icon name="down" :size="14" class="chev" />
      </button>
    </h2>
    <div v-show="open" :id="`${id}-b`" class="fb" role="region" :aria-labelledby="`${id}-h`"><slot /></div>
  </section>
</template>

<style scoped>
.fold { flex: none; overflow: hidden; }   /* in the page's scrolling column: never squeezed */
.fh { font: inherit; }
.fbtn { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 52px; padding: 10px 14px; border: 0;
  background: none; font: inherit; color: inherit; text-align: left; cursor: pointer; }
.fbtn:hover { background: var(--surface-2); }
.fbtn:focus-visible { outline-offset: -2px; border-radius: var(--radius); }
.si { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: var(--surface-2);
  color: var(--ink-2); flex: none; }
.open .si { background: var(--accent-soft); color: var(--accent); }
.tt { font-size: 14px; font-weight: 600; white-space: nowrap; }
.sub { min-width: 0; flex: 1; font-size: 12.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.aside { display: flex; align-items: center; gap: 10px; margin-left: auto; flex: none; }
.chev { color: var(--ink-3); flex: none; transition: transform .15s; }
.open .chev { transform: rotate(180deg); }
.fb { padding: 4px 14px 16px 54px; border-top: 1px solid var(--border); }
@container (max-width: 640px) {
  .sub { display: none; }
  .fb { padding-left: 14px; }
}
</style>
