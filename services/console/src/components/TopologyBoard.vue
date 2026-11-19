<script setup lang="ts">
import type {
  Dispatch,
  NodeRecord,
  SessionRecord,
  SubscriptionRecord,
  Ticket,
} from "../api";
import MeshGraph from "./MeshGraph.vue";

defineProps<{
  nodes: NodeRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
  subscriptions: SubscriptionRecord[];
}>();
</script>

<template>
  <MeshGraph
    :nodes="nodes"
    :tickets="tickets"
    :dispatches="dispatches"
    :sessions="sessions"
    :subscriptions="subscriptions"
  />
</template>
