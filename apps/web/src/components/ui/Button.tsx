"use client";

import { motion, type HTMLMotionProps } from "framer-motion";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

const VARIANT_CLASSES = {
  primary:
    "bg-accent-blue text-white shadow-sm shadow-accent-blue/20 hover:bg-accent-blue/90 disabled:hover:bg-accent-blue",
  secondary:
    "bg-surface-raised text-ink border border-border hover:border-accent-blue/40 hover:text-accent-blue",
  ghost: "bg-transparent text-ink-secondary hover:bg-surface-raised hover:text-ink",
  destructive: "bg-accent-red text-white hover:bg-accent-red/90",
} as const;

const SIZE_CLASSES = {
  sm: "h-8 px-3 text-sm gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-base gap-2",
} as const;

export interface ButtonProps extends Omit<HTMLMotionProps<"button">, "ref"> {
  variant?: keyof typeof VARIANT_CLASSES;
  size?: keyof typeof SIZE_CLASSES;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", disabled, children, ...props }, ref) => {
    return (
      <motion.button
        ref={ref}
        type="button"
        disabled={disabled}
        whileTap={disabled ? undefined : { scale: 0.97 }}
        transition={{ duration: 0.12 }}
        className={cn(
          "inline-flex items-center justify-center rounded-full font-medium transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-50",
          VARIANT_CLASSES[variant],
          SIZE_CLASSES[size],
          className
        )}
        {...props}
      >
        {children}
      </motion.button>
    );
  }
);
Button.displayName = "Button";
