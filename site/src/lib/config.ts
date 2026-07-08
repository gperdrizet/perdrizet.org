/**
 * Loads and types site config YAML for use in Astro pages and components.
 * Reads only data/user/config.yaml.
 * Import this instead of reading the YAML file directly.
 */
import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

const USER_CONFIG_PATH = path.resolve(process.cwd(), '../data/user/config.yaml');

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
  home_sections?: Array<{
    collection: string;
    title?: string;
    summary?: string;
  }>;
  about_sections?: Array<{
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
    if (!fs.existsSync(USER_CONFIG_PATH)) {
      throw new Error('Missing required file: data/user/config.yaml');
    }
    const raw = fs.readFileSync(USER_CONFIG_PATH, 'utf-8');
    _config = yaml.load(raw) as SiteConfig;
  }
  return _config;
}
