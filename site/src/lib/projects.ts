/**
 * Loads and types data/projects.yaml for use in Astro pages and components.
 */
import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

const PROJECTS_PATH = path.resolve(process.cwd(), '../data/projects.yaml');

export type ProjectStatus = 'live' | 'published' | 'wip' | 'archived';

export type ProjectRole =
  | 'llm-engineer'
  | 'ml-engineer'
  | 'data-scientist'
  | 'educator'
  | 'backend-engineer'
  | 'researcher'
  | 'devops';

export interface Project {
  name: string;
  display_name: string;
  status: ProjectStatus;
  featured?: boolean;
  tags: string[];
  roles: ProjectRole[];
  github?: string;
  service_url?: string;
  package_url?: string;
  description_short: string;
  description_long?: string;
  teaching_context?: string;
  highlights?: string[];
}

interface ProjectsFile {
  projects: Project[];
}

let _projects: Project[] | null = null;

export function getProjects(): Project[] {
  if (!_projects) {
    const raw = fs.readFileSync(PROJECTS_PATH, 'utf-8');
    const data = yaml.load(raw) as ProjectsFile;
    _projects = data.projects;
  }
  return _projects;
}

export function getFeaturedProjects(): Project[] {
  return getProjects().filter((p) => p.featured);
}

export function getProjectsByRole(role: ProjectRole): Project[] {
  return getProjects().filter((p) => p.roles.includes(role));
}

export function getProjectsByStatus(status: ProjectStatus): Project[] {
  return getProjects().filter((p) => p.status === status);
}

export function getProjectBySlug(slug: string): Project | undefined {
  return getProjects().find((p) => p.name === slug);
}
