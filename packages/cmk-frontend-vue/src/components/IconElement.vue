<!--
Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
-->
<script setup lang="ts">
import { type VariantProps, cva } from 'class-variance-authority'

const iconElementVariants = cva('', {
  variants: {
    variant: {
      plain: '',
      inline: 'icon-element--inline'
    },
    size: {
      xsmall: '10px',
      small: '12px',
      medium: '15px',
      large: '18px',
      xlarge: '20px',
      xxlarge: '32px'
    }
  },
  defaultVariants: {
    variant: 'plain',
    size: 'medium'
  }
})
export type IconElementVariants = VariantProps<typeof iconElementVariants>

interface IconElementProps {
  /** @property {string} name - Name of the icon */
  name: string

  /** @property {undefined | IconElementVariants['variant']} variant - Styling variant of the icon */
  variant?: IconElementVariants['variant']

  /** @property {undefined | IconElementVariants['size']} size - Width and height of the icon */
  size?: IconElementVariants['size']

  /** @property {undefined | number} rotate - Transform rotate value in degrees */
  rotate?: number

  /** @property {undefined | string} title - Title to be displayed on hover */
  title?: string
}

const props = defineProps<IconElementProps>()

const getTransformRotate = () => {
  return `rotate(${props.rotate || 0}deg)`
}

const getIconVariable = (iconName: string | undefined): string => {
  /*
     Transform from icon file name pattern
        "icon_<underscored_name>.<file_extension>" or "<underscored_name>.<file_extension>"
     to CSS variable name pattern, returned as a call to the CSS fct var()
        "var(--icon-<dashed_name>)"

     E.g. "icon_main_help.svg" -> "var(--icon-main-help)"
  */
  if (!iconName) {
    return 'none'
  }

  let iconVar: string = `${iconName.startsWith('icon') ? iconName : ['icon', iconName].join('-')}`
  iconVar = iconVar.replace(/_/g, '-').split('.')[0]!
  return `var(--${iconVar})`
}
</script>

<template>
  <img
    class="icon-element"
    :class="iconElementVariants({ variant: props.variant, size: null })"
    :title="title || ''"
    :alt="title || ''"
  />
</template>

<style scoped>
.icon-element {
  margin: 0;
  padding: 0;
  vertical-align: baseline;

  content: v-bind(getIconVariable(name));
  width: v-bind(iconElementVariants({size}));
  height: v-bind(iconElementVariants({size}));
  transform: v-bind(getTransformRotate());

  &.icon-element--inline {
    margin-right: var(--spacing-half);
    vertical-align: middle;
  }
}
</style>
