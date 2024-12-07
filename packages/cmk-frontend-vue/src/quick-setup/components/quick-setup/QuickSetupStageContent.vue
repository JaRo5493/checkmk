<!--
Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
-->
<script setup lang="ts">
import { computed } from 'vue'
import Button from '@/components/IconButton.vue'
import IconElement from '@/components/IconElement.vue'
import AlertBox from '@/components/AlertBox.vue'
import type { QuickSetupStageContent } from './quick_setup_types'
import { type ButtonVariants } from '@/components/IconButton.vue'

const props = defineProps<QuickSetupStageContent>()

const isLast = computed(() => props.index === props.numberOfStages - 1)
const isSaveOverview = computed(
  () => props.index === props.numberOfStages && props.mode === 'overview'
)
const showButtons = computed(() => props.mode === 'guided' || isSaveOverview.value)
const filteredButtons = computed(() =>
  props.buttons.filter((b) => !isSaveOverview.value || b.variant === 'save')
)

function getButtonAriaLabel(variant: ButtonVariants['variant']): string {
  /* TODO: move this strings to the backend to make it translatable (CMK-19020) */
  switch (variant) {
    case 'prev':
      return 'Go to the previous stage'
    case 'next':
      return 'Go to the next stage'
    case 'save':
      return 'Save'
  }
  return ''
}
</script>

<template>
  <div>
    <component :is="content" v-if="content" />

    <AlertBox v-if="errors && errors.length > 0" variant="error">
      <p v-for="error in errors" :key="error">{{ error }}</p>
    </AlertBox>

    <div v-if="showButtons">
      <div v-if="!loading" class="qs-stage-content__action">
        <Button
          v-for="button in filteredButtons"
          :key="button.label"
          :label="button.label"
          :aria-label="getButtonAriaLabel(button.variant)"
          :variant="button.variant"
          @click="button.action"
        />
      </div>
      <div v-else class="qs-stage-content__loading">
        <IconElement name="load-graph" variant="inline" size="xlarge" />
        <!-- TODO: move these texts to the backend to make them translatable (CMK-19020) -->
        <span v-if="isLast">This process may take several minutes, please wait...</span>
        <span v-else>Please wait...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qs-stage-content__action {
  padding-top: var(--spacing);
  position: relative;
}

.qs-stage-content__loading {
  display: flex;
  align-items: center;
  padding-top: 12px;
}
</style>
