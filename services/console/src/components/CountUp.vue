<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

const props = withDefaults(defineProps<{ value: number; duration?: number }>(), {
  duration: 620,
});

const shown = ref(props.value);
let raf = 0;

watch(
  () => props.value,
  (to, from) => {
    cancelAnimationFrame(raf);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      shown.value = to;
      return;
    }
    const started = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - started) / props.duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      shown.value = Math.round(from + (to - from) * eased);
      if (progress < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  },
);

onBeforeUnmount(() => cancelAnimationFrame(raf));
</script>

<template>
  <span class="mono">{{ shown }}</span>
</template>
