<!-- 登录表单：账号登录 / 飞书授权 两个 Tab（自绘下划线） -->
<template>
  <div>
    <!-- 自绘下划线 Tab 头 -->
    <div class="login-tabs" role="tablist">
      <button
        type="button"
        class="login-tab"
        :class="{ 'login-tab--active': activeTab === 'account' }"
        role="tab"
        :aria-selected="activeTab === 'account'"
        @click="activeTab = 'account'"
      >
        {{ $t("login.tab.account") }}
      </button>
      <button
        type="button"
        class="login-tab"
        :class="{ 'login-tab--active': activeTab === 'feishu' }"
        role="tab"
        :aria-selected="activeTab === 'feishu'"
        @click="activeTab = 'feishu'"
      >
        {{ $t("login.tab.feishu") }}
      </button>
      <span
        class="login-tab-indicator"
        :class="activeTab === 'feishu' ? 'login-tab-indicator--right' : 'login-tab-indicator--left'"
      />
    </div>

    <ElForm
      v-show="activeTab === 'account'"
      ref="formRef"
      :model="loginForm"
      :rules="rules"
      :key="formKey"
      class="login-page-form"
      :validate-on-rule-change="false"
      @keyup.enter="$emit('submit')"
    >
      <ElFormItem prop="username">
        <ElInput
          class="custom-height"
          v-model.trim="loginForm.username"
          clearable
          :placeholder="$t('login.placeholder.username')"
        >
          <template #prefix>
            <ElIcon><User /></ElIcon>
          </template>
        </ElInput>
      </ElFormItem>

      <ElTooltip :visible="isCapsLock" :content="$t('login.capsLock')" placement="right">
        <ElFormItem prop="password">
          <ElInput
            class="custom-height"
            v-model.trim="loginForm.password"
            type="password"
            autocomplete="off"
            show-password
            clearable
            :placeholder="$t('login.placeholder.password')"
            @keyup="onPasswordKeyup"
          >
            <template #prefix>
              <ElIcon><Lock /></ElIcon>
            </template>
          </ElInput>
        </ElFormItem>
      </ElTooltip>

      <div class="login-form-tail flex flex-col gap-[1.1rem]">
        <div class="login-options-row flex items-center justify-between text-sm">
          <ElCheckbox v-model="loginForm.remember" class="login-remember">
            {{ $t("login.rememberPwd") }}
          </ElCheckbox>
          <ElLink
            type="primary"
            underline="never"
            class="inline-flex items-center text-sm leading-[inherit]!"
            @click="$emit('forget')"
          >
            {{ $t("login.forgetPwd") }}
          </ElLink>
        </div>

        <div>
          <ElButton
            class="h-11 w-full rounded-lg! text-base font-medium"
            type="primary"
            :loading="loading"
            v-ripple
            @click="$emit('submit')"
          >
            {{ $t("login.btnText") }}
          </ElButton>
        </div>

        <div v-if="SHOW_SAAS_AUTH" class="login-secondary-actions grid grid-cols-2 gap-2">
          <ElButton class="login-secondary-btn" plain @click="$emit('openMobile')">
            {{ $t("login.mobileLogin") }}
          </ElButton>
          <ElButton class="login-secondary-btn" plain @click="$emit('openQr')">
            {{ $t("login.qrLogin") }}
          </ElButton>
        </div>
      </div>
    </ElForm>

    <!-- 飞书授权 Tab：仅一个授权按钮 -->
    <div v-show="activeTab === 'feishu'" class="login-feishu-panel">
      <ElButton
        class="login-feishu-auth-btn h-11 w-full rounded-lg! text-base font-medium"
        type="primary"
        plain
        v-ripple
        @click="$emit('oauth', 'feishu')"
      >
        <FaSvgIcon icon="simple-icons:feishu" class="mr-2 size-[18px] text-[#00d6b9]" />
        {{ $t("login.feishuAuthBtn") }}
      </ElButton>
    </div>

    <FaLoginThirdPartySection v-if="SHOW_SAAS_AUTH" @oauth="$emit('oauth', $event)" />

    <FaLoginAuthLinkRow
      v-if="SHOW_SAAS_AUTH"
      :hint="$t('login.noAccount')"
      :link-text="$t('login.register')"
      @link="$emit('register')"
    />
  </div>
</template>

<script setup lang="ts">
import { Lock, User } from "@element-plus/icons-vue";
import type { LoginFormData } from "@/api/module_system/auth";
import type { FormRules } from "element-plus";

const loginForm = defineModel<LoginFormData>("loginForm", { required: true });

defineOptions({ name: "FaLoginAccountForm" });

interface Props {
  rules: FormRules;
  formKey: number | string;
  loading: boolean;
}

withDefaults(defineProps<Props>(), {});

interface Emits {
  submit: [];
  openMobile: [];
  openQr: [];
  forget: [];
  register: [];
  oauth: [provider: "wechat" | "qq" | "github" | "gitee" | "feishu"];
}

const emit = defineEmits<Emits>();

/** 内部控制面：隐藏注册/第三方/手机扫码/演示账号等 SaaS 登录入口，仅留账号密码 */
const SHOW_SAAS_AUTH = false;

const formRef = ref();
const isCapsLock = ref(false);
const activeTab = ref<"account" | "feishu">("account");

function onPasswordKeyup(event: KeyboardEvent) {
  if (event instanceof KeyboardEvent) {
    isCapsLock.value = event.getModifierState("CapsLock");
    if (event.key === "Enter") {
      emit("submit");
    }
  }
}

defineExpose({
  validate: () => formRef.value?.validate?.(),
  clearValidate: () => formRef.value?.clearValidate?.(),
});
</script>

<style scoped lang="scss">
@use "../fa-login";

/* 自绘下划线 Tab */
.login-tabs {
  position: relative;
  display: flex;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.login-tab {
  flex: 1;
  padding: 0.65rem 0;
  font-size: 0.95rem;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  background: transparent;
  border: none;
  transition: color 0.22s ease;

  &:hover {
    color: var(--el-text-color-primary);
  }

  &--active {
    font-weight: 600;
    color: var(--el-color-primary);
  }
}

.login-tab-indicator {
  position: absolute;
  bottom: -1px;
  width: 50%;
  height: 2px;
  background: var(--el-color-primary);
  border-radius: 2px;
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);

  &--left {
    transform: translateX(0);
  }

  &--right {
    transform: translateX(100%);
  }
}

/* 飞书授权面板 */
.login-feishu-panel {
  padding-top: 0.5rem;
}
</style>
