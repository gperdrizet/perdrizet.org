/**
 * Loads and types site config YAML for use in Astro pages and components.
 * Reads only data/user/profile.yaml.
 * Import this instead of reading the YAML file directly.
 */
import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

const USER_PROFILE_PATH = path.resolve(process.cwd(), '../data/user/profile.yaml');
const LEGACY_CONFIG_PATH = path.resolve(process.cwd(), '../data/user/config.yaml');
const USER_PROJECTS_PATH = path.resolve(process.cwd(), '../data/user/projects.yaml');

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
}

const DEFAULT_PROFILE: SiteConfig = {
  personal: {
    name: 'Your Name',
    tagline: 'Set your profile in admin onboarding',
    domain: '',
    email: '',
    github_username: '',
    social: {},
  },
  bio: {
    short: 'Complete onboarding in the admin interface to publish your profile.',
    long: '',
  },
  home_sections: [],
  about_sections: [],
};

let _config: SiteConfig | null = null;

export function getConfig(): SiteConfig {
  if (!_config) {
    if (!fs.existsSync(USER_PROFILE_PATH) && fs.existsSync(LEGACY_CONFIG_PATH)) {
      fs.mkdirSync(path.dirname(USER_PROFILE_PATH), { recursive: true });
      fs.copyFileSync(LEGACY_CONFIG_PATH, USER_PROFILE_PATH);
    }

    if (!fs.existsSync(USER_PROFILE_PATH)) {
      _config = DEFAULT_PROFILE;
      return _config;
    }
    const raw = fs.readFileSync(USER_PROFILE_PATH, 'utf-8');
    const loaded = yaml.load(raw) as Partial<SiteConfig> | null;
    _config = {
      ...DEFAULT_PROFILE,
      ...(loaded ?? {}),
      personal: {
        ...DEFAULT_PROFILE.personal,
        ...(loaded?.personal ?? {}),
        social: {
          ...(DEFAULT_PROFILE.personal.social ?? {}),
          ...(loaded?.personal?.social ?? {}),
        },
      },
      bio: {
        ...DEFAULT_PROFILE.bio,
        ...(loaded?.bio ?? {}),
      },
    };
  }
  return _config;
}

export function isOnboardingRequired(): boolean {
  if (!fs.existsSync(USER_PROFILE_PATH) && !fs.existsSync(LEGACY_CONFIG_PATH)) {
    return true;
  }
  if (!fs.existsSync(USER_PROJECTS_PATH)) {
    return true;
  }
  return false;
}

export function getAdminUrl(config: SiteConfig): string {
  const domain = config.personal.domain?.trim();
  if (domain) {
    return `https://admin.${domain}/`;
  }
  return 'http://127.0.0.1:8600/';
}

export function getAdminOnboardingUrl(config: SiteConfig): string {
  const adminUrl = new URL(getAdminUrl(config));
  adminUrl.searchParams.set('onboarding', '1');
  return adminUrl.toString();
}
