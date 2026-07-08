/**
 * Loads and types data/config.yaml for use in Astro pages and components.
 * Import this instead of reading the YAML file directly.
 */
import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

const CONFIG_PATH = path.resolve(process.cwd(), '../data/config.yaml');

export interface SocialLinks {
  linkedin?: string;
  twitter?: string;
  bluesky?: string;
  substack?: string;
  github?: string;
}

export interface SiteConfig {
  personal: {
    name: string;
    tagline: string;
    domain: string;
    email: string;
    github_username: string;
    social: SocialLinks;
  };
  bio: {
    short: string;
    long?: string;
  };
  teaching: {
    active: boolean;
    summary: string;
    platforms: string[];
    topics: string[];
  };
  home_sections?: Array<{
    collection: string;
    title?: string;
    summary?: string;
  }>;
  llm: {
    base_url: string;
    model: string;
  };
  deploy: {
    staging_path: string;
    prod_path: string;
    staging_port: number;
  };
}

let _config: SiteConfig | null = null;

export function getConfig(): SiteConfig {
  if (!_config) {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    _config = yaml.load(raw) as SiteConfig;
  }
  return _config;
}
