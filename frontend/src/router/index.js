import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/auth'

const routes = [
  { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { guest: true } },
  { path: '/register', name: 'Register', component: () => import('../views/Register.vue'), meta: { guest: true } },
  { path: '/', name: 'Home', component: () => import('../views/Home.vue'), meta: { requiresAuth: true } },
  { path: '/dashboard/:animeId', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), props: true, meta: { requiresAuth: true } },
  { path: '/history', name: 'History', component: () => import('../views/HistoryPage.vue'), meta: { requiresAuth: true } },
  { path: '/agent', name: 'AgentCenter', component: () => import('../views/AgentCenter.vue'), meta: { requiresAuth: true } },
  { path: '/agent/opinion', name: 'OpinionAgent', component: () => import('../views/OpinionAgentPage.vue'), meta: { requiresAuth: true } },
  { path: '/agent/recommendation', name: 'RecommendationAgent', component: () => import('../views/RecommendationAgentPage.vue'), meta: { requiresAuth: true } },
  { path: '/agent/evaluation', name: 'RagEvaluation', component: () => import('../views/RagEvaluationPage.vue'), meta: { requiresAuth: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, _from, next) => {
  const loggedIn = isLoggedIn()
  if (to.meta.requiresAuth && !loggedIn) {
    return next({ name: 'Login' })
  }
  if (to.meta.guest && loggedIn) {
    return next({ name: 'Home' })
  }
  next()
})

export default router

