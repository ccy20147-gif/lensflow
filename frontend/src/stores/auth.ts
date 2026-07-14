import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  bootstrapIdentity,
  clearAuthToken,
  getBootstrapStatus,
  getStoredAccount,
  getAuthToken,
  loginIdentity,
  logoutIdentity,
  registerIdentity,
  setAuthToken,
  setStoredAccount,
  verifyToken,
  type AccountRef,
  type LoginRequest,
  type RegisterRequest,
} from '@/api/client'

/**
 * Auth store — explicit login/register/logout/verify flow. We deliberately
 * do NOT auto-bootstrap the single owner; the UI requires the user to
 * bootstrap or register on first run, mirroring what production feels like.
 */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getAuthToken())
  const account = ref<AccountRef | null>(getStoredAccount())
  const bootstrapCompleted = ref<boolean | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => Boolean(token.value))

  async function refreshBootstrapStatus(): Promise<boolean> {
    const status = await getBootstrapStatus()
    bootstrapCompleted.value = status.completed
    return status.completed
  }

  async function login(creds: LoginRequest): Promise<AccountRef> {
    loading.value = true
    error.value = null
    try {
      const result = await loginIdentity(creds)
      setAuthToken(result.token)
      token.value = result.token
      const acct: AccountRef = {
        account_id: result.account_id,
        email: creds.email,
      }
      setStoredAccount(acct)
      account.value = acct
      return acct
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function register(req: RegisterRequest): Promise<AccountRef> {
    loading.value = true
    error.value = null
    try {
      const result = await registerIdentity(req)
      setStoredAccount({
        account_id: result.account_id,
        email: result.email,
        display_name: result.display_name,
      })
      account.value = {
        account_id: result.account_id,
        email: result.email,
        display_name: result.display_name,
      }
      return account.value
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function bootstrap(req: {
    email: string
    display_name: string
    password: string
  }): Promise<unknown> {
    loading.value = true
    error.value = null
    try {
      const result = await bootstrapIdentity(req)
      bootstrapCompleted.value = true
      return result
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await logoutIdentity().catch(() => {
        // Even if backend revoke fails, drop the local session silently.
      })
    } finally {
      clearAuthToken()
      token.value = null
      account.value = null
      loading.value = false
    }
  }

  async function verify(): Promise<AccountRef | null> {
    if (!token.value) return null
    try {
      const result = await verifyToken()
      return { account_id: result.account_id, email: account.value?.email ?? '' }
    } catch {
      // Token is invalid — clear the session so the router redirects to login.
      clearAuthToken()
      token.value = null
      account.value = null
      return null
    }
  }

  return {
    token,
    account,
    bootstrapCompleted,
    loading,
    error,
    isAuthenticated,
    refreshBootstrapStatus,
    login,
    register,
    bootstrap,
    logout,
    verify,
  }
})
