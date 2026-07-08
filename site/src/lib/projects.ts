/**
 * Loads and types project YAML for use in Astro pages and components.
 * Reads only data/user/projects.yaml.
 */
import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

const USER_PROJECTS_PATH = path.resolve(process.cwd(), '../data/user/projects.yaml');

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
  kind?: 'project';
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

export interface CollectionMemberProject {
  project: string;
}

export interface CollectionMemberRepo {
  repo: string;
  label?: string;
  url?: string;
}

export type CollectionMember = CollectionMemberProject | CollectionMemberRepo;

export interface Collection {
  kind: 'collection';
  name: string;
  display_name: string;
  type?: string;
  featured?: boolean;
  tags?: string[];
  roles?: ProjectRole[];
  summary?: string;
  description_short?: string;
  description_long?: string;
  topics?: string[];
  platforms?: string[];
  members?: CollectionMember[];
}

export type ContentEntry = Project | Collection;

interface ProjectsFile {
  projects: ContentEntry[];
}

let _entries: ContentEntry[] | null = null;

function getEntries(): ContentEntry[] {
  if (!_entries) {
    if (!fs.existsSync(USER_PROJECTS_PATH)) {
      throw new Error('Missing required file: data/user/projects.yaml');
    }
    const raw = fs.readFileSync(USER_PROJECTS_PATH, 'utf-8');
    const data = yaml.load(raw) as ProjectsFile;
    _entries = data.projects ?? [];
  }
  return _entries;
}

function isCollection(entry: ContentEntry): entry is Collection {
  return (entry as Collection).kind === 'collection';
}

function isProject(entry: ContentEntry): entry is Project {
  return !isCollection(entry);
}

export function getProjects(): Project[] {
  return getEntries().filter(isProject);
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

export function getCollections(): Collection[] {
  return getEntries().filter(isCollection);
}

export function getCollectionBySlug(slug: string): Collection | undefined {
  return getCollections().find((c) => c.name === slug);
}

export function getFeaturedCollections(): Collection[] {
  return getCollections().filter((c) => c.featured);
}

export function resolveCollectionProjects(collection: Collection): Project[] {
  const members = collection.members ?? [];
  const bySlug = new Map(getProjects().map((p) => [p.name, p]));
  const projects: Project[] = [];
  for (const member of members) {
    if ('project' in member) {
      const project = bySlug.get(member.project);
      if (project) {
        projects.push(project);
      }
    }
  }
  return projects;
}

export function resolveCollectionRepos(collection: Collection): CollectionMemberRepo[] {
  const members = collection.members ?? [];
  return members.filter((member): member is CollectionMemberRepo => 'repo' in member);
}
