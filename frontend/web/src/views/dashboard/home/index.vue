<template>
  <div class="home-page flex flex-col gap-5">
    <FaDashboardSkeleton v-if="loading" />
    <template v-else>
      <WelcomeBanner />
      <FeatureGrid />
      <QuickLinks />
    </template>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: "Home", inheritAttrs: false });

import { ref, onMounted } from "vue";
import FaDashboardSkeleton from "@/components/skeleton/fa-dashboard-skeleton.vue";
import WelcomeBanner from "./modules/welcome-banner.vue";
import FeatureGrid from "./modules/feature-grid.vue";
import QuickLinks from "./modules/quick-links.vue";

const loading = ref(true);
onMounted(() => {
  loading.value = false;
});
</script>

<style scoped lang="scss"></style>
