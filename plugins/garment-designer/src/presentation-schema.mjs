import { z } from 'zod';
import { id } from './model.mjs';

export const presentationSlideSchema = z.object({
  title: z.string().min(1).max(60),
  visual: z.enum(['overview', 'front', 'back', 'part', 'palette']).default('overview'),
  part_id: id.optional(),
  bullets: z.array(z.string().min(1).max(120)).max(5).default([]),
  notes: z.string().max(4000).default(''),
}).strict().superRefine((slide, ctx) => {
  if (slide.visual === 'part' && !slide.part_id) ctx.addIssue({ code: 'custom', message: 'A detail slide requires part_id.' });
  if (slide.visual !== 'part' && slide.part_id) ctx.addIssue({ code: 'custom', message: 'part_id is only used by a part detail slide.' });
  if (slide.bullets.join('').length > 280) ctx.addIssue({ code: 'custom', message: 'Keep slide bullets within 280 characters; put explanation in notes or split the slide.' });
});

export const presentationSchema = z.object({
  project_id: id,
  revision: z.number().int().positive(),
  formats: z.array(z.enum(['pdf', 'pptx', 'html'])).min(1).max(3).default(['pdf', 'pptx', 'html']),
  language: z.enum(['zh', 'en']).default('zh'),
  title: z.string().min(1).max(160).optional(),
  slides: z.array(presentationSlideSchema).min(1).max(24).optional(),
}).strict();
