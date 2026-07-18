<template>
  <div id="app-root">
    <router-view v-slot="{ Component }">
      <transition name="page" mode="out-in">
        <component :is="Component" />
      </transition>
    </router-view>
  </div>
</template>

<script setup>
</script>

<style>
#app-root {
  min-height: 100vh;
  background: var(--bg-deep);
}

.page-enter-active {
  animation: pageIn 0.4s ease-out;
}
.page-leave-active {
  animation: pageOut 0.25s ease-in;
}
@keyframes pageIn {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes pageOut {
  from { opacity: 1; }
  to   { opacity: 0; }
}
</style>
