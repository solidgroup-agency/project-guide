import os
import anthropic
import logging
import json
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Exclusion list
EXCLUSION_LIST = ['.git', '.venv', 'node_modules', '__pycache__', '.DS_Store', 'pb_data', 'pb_public', 'migrations']

class ProjectAnalyzer:
    def __init__(self, project_dir):
        # Base directories
        self.project_dir = Path(project_dir)
        self.script_dir = Path(__file__).parent
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directories
        self.findings_dir = self.script_dir / 'findings' / self.timestamp
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up file paths
        self.findings_path = self.findings_dir / 'findings.json'
        self.initial_summaries_path = self.script_dir / f'initial-summaries_{self.timestamp}.txt'
        
        # Initialize anthropic client
        self.client = anthropic.Anthropic()
        
        # Initialize findings file
        self._init_findings_file()
        
        logging.info(f"Initialized ProjectAnalyzer:")
        logging.info(f"- Project directory: {self.project_dir}")
        logging.info(f"- Script directory: {self.script_dir}")
        logging.info(f"- Findings directory: {self.findings_dir}")
        logging.info(f"- Initial summaries: {self.initial_summaries_path}")
        logging.info(f"- Findings JSON: {self.findings_path}")

    def _init_findings_file(self):
        """Initialize the findings JSON file with basic structure"""
        initial_structure = {
            'root_summary': '',
            'directories': {},
            'files': {}
        }
        self._write_findings(initial_structure)

    def _write_findings(self, data):
        """Write or update findings JSON file"""
        with open(self.findings_path, 'w') as f:
            json.dump(data, f, indent=4)

    def _append_to_summaries(self, content):
        """Append content to the initial summaries file"""
        with open(self.initial_summaries_path, 'a') as f:
            f.write(f"{content}\n\n")

    def _read_findings(self):
        """Read current findings from JSON file"""
        with open(self.findings_path, 'r') as f:
            return json.load(f)

    def _update_findings(self, key, value):
        """Update specific section in findings"""
        findings = self._read_findings()
        if isinstance(key, tuple):  # For nested updates
            current = findings
            for k in key[:-1]:
                current = current.setdefault(k, {})
            current[key[-1]] = value
        else:
            findings[key] = value
        self._write_findings(findings)

    def is_excluded(self, path):
        return any(excluded in str(path) for excluded in EXCLUSION_LIST)

    def analyze_root(self):
        logging.info("Analyzing root directory...")
        
        # Get only top-level files and directories instead of recursively getting all
        root_items = [f for f in Path(self.project_dir).iterdir() if not self.is_excluded(f)]
        
        # Count files by extension to infer language
        file_extensions = {}
        for root, dirs, files in os.walk(self.project_dir):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if not self.is_excluded(d)]
            
            for file in files:
                if self.is_excluded(file):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext:
                    file_extensions[ext] = file_extensions.get(ext, 0) + 1
        
        # Sort by count and take top 10
        top_extensions = sorted(file_extensions.items(), key=lambda x: x[1], reverse=True)[:10]
        extensions_str = "\n".join([f"{ext}: {count} files" for ext, count in top_extensions])
        
        # Get only top-level structure for analysis
        root_structure = []
        for item in root_items:
            if item.is_dir():
                # For directories, show count of files
                file_count = sum(1 for _ in Path(item).rglob('*') if _.is_file() and not self.is_excluded(_))
                root_structure.append(f"{item.name}/ (directory with {file_count} files)")
            else:
                root_structure.append(f"{item.name}")
        
        root_contents_str = '\n'.join(root_structure)

        message = self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            temperature=0,
            system="You are an AI assistant that summarizes the main language and purpose of a project.",
            messages=[{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": f"Project directory: {self.project_dir}\n\n"
                    f"Top file extensions:\n{extensions_str}\n\n"
                    f"Top-level structure:\n{root_contents_str}\n\n"
                    "Based on the directory structure, file counts, and extensions, what is the main language used in this project? "
                    "What is the project's purpose? Please provide a comprehensive summary."
                }]
            }]
        )

        summary = message.content[0].text
        self._update_findings('root_summary', summary)
        self._append_to_summaries(f"Project Overview:\n{summary}")
        logging.info("Root analysis complete")

    def analyze_file(self, file_path):
        rel_path = str(Path(file_path).relative_to(self.project_dir))
        logging.info(f"Analyzing file: {rel_path}")

        try:
            # Get file info
            file_size = os.path.getsize(file_path)
            file_extension = os.path.splitext(file_path)[1].lower()
            
            # Skip binary files and files that are too large
            if file_extension in ['.pyc', '.so', '.dll', '.exe', '.bin', '.jpg', '.png', '.gif']:
                logging.info(f"Skipping binary file: {rel_path}")
                return f"Binary file (skipped analysis)"
                
            # For very large files, truncate content
            max_file_size = 100 * 1024  # 100KB max for analysis
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    if file_size > max_file_size:
                        # Read beginning and end of file
                        beginning = ''.join(f.readline() for _ in range(100))
                        
                        # Get to the last part of the file
                        f.seek(max(0, file_size - max_file_size // 2))
                        # Clear the current line (it might be partial)
                        f.readline()
                        # Read the rest
                        ending = f.read()
                        
                        content = f"{beginning}\n\n... [truncated {file_size - max_file_size} bytes] ...\n\n{ending}"
                        content_description = f"(truncated, showing first and last parts of {file_size} bytes)"
                    else:
                        content = f.read()
                        content_description = "(full content)"
            except UnicodeDecodeError:
                logging.info(f"Skipping binary file (Unicode decode error): {rel_path}")
                return f"Binary file (skipped analysis)"

            message = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                temperature=0,
                system="You are an AI assistant that analyzes source code files.",
                messages=[{
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": f"Analyze this file: {rel_path} {content_description}\n\nContent:\n{content}\n\n"
                        "Please provide:\n"
                        "1. Overall purpose of the file\n"
                        "2. Key fields/variables and their purposes (not all, just the most important ones)\n"
                        "3. Main function definitions with inputs, outputs, and purposes\n"
                        "4. Any important structs/classes and their significance\n"
                        "5. How this file fits into the project"
                    }]
                }]
            )

            summary = message.content[0].text
            self._update_findings(('files', rel_path), summary)
            self._append_to_summaries(f"File: {rel_path}\n{summary}")
            logging.info(f"Completed analysis of file: {rel_path}")
            return summary

        except Exception as e:
            logging.error(f"Error analyzing file {file_path}: {str(e)}")
            return None

    def analyze_directory(self, dir_path):
        if self.is_excluded(dir_path):
            return

        rel_path = str(Path(dir_path).relative_to(self.project_dir))
        logging.info(f"Analyzing directory: {rel_path}")

        try:
            # Get list of files in directory
            files = [f for f in Path(dir_path).iterdir() 
                    if f.is_file() and not self.is_excluded(f)]

            if not files:  # Skip empty directories
                return

            # For directories with many files, limit how many we analyze in detail
            max_files_to_analyze = 10
            if len(files) > max_files_to_analyze:
                logging.info(f"Directory {rel_path} has {len(files)} files, analyzing only {max_files_to_analyze}")
                # Sort files by size and get a sample (mix of smallest and largest)
                files_by_size = sorted(files, key=lambda f: f.stat().st_size)
                sample_files = files_by_size[:max_files_to_analyze//2] + files_by_size[-max_files_to_analyze//2:]
                
                # Get stats for all files
                file_extensions = {}
                total_size = 0
                for file in files:
                    ext = os.path.splitext(file.name)[1].lower()
                    if ext:
                        file_extensions[ext] = file_extensions.get(ext, 0) + 1
                    total_size += file.stat().st_size
                
                ext_summary = ", ".join([f"{ext}: {count}" for ext, count in 
                                        sorted(file_extensions.items(), key=lambda x: x[1], reverse=True)[:5]])
                
                # Add directory summary
                directory_info = (f"Directory contains {len(files)} files "
                                 f"({total_size / 1024:.1f} KB total, main extensions: {ext_summary}). "
                                 f"Analyzing sample of {len(sample_files)} files.")
                
                files = sample_files
            else:
                directory_info = ""
                
            # Analyze each file first
            file_summaries = []
            for file in files:
                summary = self.analyze_file(str(file))
                if summary:
                    # Limit summary size to prevent token explosion
                    if len(summary) > 1000:
                        summary = summary[:500] + "..." + summary[-500:]
                    file_summaries.append(f"{file.name}: {summary}")
            
            # Join summaries but ensure we don't exceed reasonable prompt size
            combined_summaries = "\n".join(file_summaries)
            if len(combined_summaries) > 10000:  # Truncate if too long
                combined_summaries = combined_summaries[:10000] + "... [summaries truncated due to length]"

            # Analyze directory as a whole
            if file_summaries:
                message = self.client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    temperature=0,
                    system="You are an AI assistant that analyzes code directories.",
                    messages=[{
                        "role": "user",
                        "content": [{
                            "type": "text",
                            "text": f"Analyze this directory: {rel_path}\n\n{directory_info}\n\nFiles:\n{combined_summaries}\n\n"
                            "Please provide a summary of this directory's purpose and how its contents work together."
                        }]
                    }]
                )

                summary = message.content[0].text
                self._update_findings(('directories', rel_path), summary)
                self._append_to_summaries(f"Directory: {rel_path}\n{summary}")
                logging.info(f"Completed analysis of directory: {rel_path}")

        except Exception as e:
            logging.error(f"Error analyzing directory {dir_path}: {str(e)}")

    def analyze_project(self):
        """First phase: analyze the project and collect initial summaries"""
        logging.info(f"Starting analysis of project: {self.project_dir}")
        
        # Analyze root first
        self.analyze_root()
        
        # Recursively analyze directories and files
        for root, dirs, files in os.walk(self.project_dir):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not self.is_excluded(d)]
            
            # Analyze current directory
            self.analyze_directory(root)

        logging.info("Project analysis complete")
        return self.initial_summaries_path, self.findings_path

    def generate_developer_guide(self):
        """Second phase: generate a well-organized developer guide in markdown"""
        logging.info("Generating developer guide...")
        
        # Read the collected data
        with open(self.findings_path, 'r') as f:
            findings = json.load(f)
        
        with open(self.initial_summaries_path, 'r') as f:
            initial_summaries = f.read()

        # Generate the final guidebook
        guidebook_content = self._create_markdown_guide(findings, initial_summaries)
        
        # Write the final guidebook
        guidebook_path = self.script_dir / f'guidebook_{self.timestamp}.md'
        with open(guidebook_path, 'w') as f:
            f.write(guidebook_content)
        
        return guidebook_path

    def _create_markdown_guide(self, findings, initial_summaries):
        """Create the markdown developer guide using collected data"""
        
        # Truncate initial summaries if too large
        if len(initial_summaries) > 20000:
            logging.warning(f"Initial summaries too large ({len(initial_summaries)} chars), truncating")
            summary_lines = initial_summaries.split('\n')
            # Keep the project overview and a sample of other summaries
            important_sections = []
            
            # Always include the project overview
            project_overview_section = []
            in_overview = False
            for line in summary_lines:
                if line.startswith("Project Overview:"):
                    in_overview = True
                if in_overview:
                    project_overview_section.append(line)
                if in_overview and not line.strip():
                    in_overview = False
                    
            important_sections.extend(project_overview_section)
            
            # Take a sample of other sections
            other_sections = []
            current_section = []
            for line in summary_lines:
                if line.startswith("File:") or line.startswith("Directory:"):
                    if current_section:
                        other_sections.append('\n'.join(current_section))
                        current_section = []
                current_section.append(line)
            
            if current_section:
                other_sections.append('\n'.join(current_section))
                
            # Take a sample of sections (beginning, middle, end)
            max_sections = 30
            if len(other_sections) > max_sections:
                sampled_sections = []
                sampled_sections.extend(other_sections[:max_sections//3])  # Beginning
                sampled_sections.extend(other_sections[len(other_sections)//2:len(other_sections)//2 + max_sections//3])  # Middle
                sampled_sections.extend(other_sections[-max_sections//3:])  # End
                other_sections = sampled_sections
                
            # Join everything
            truncated_summaries = '\n\n'.join(important_sections + ['\n'.join(other_sections)])
            truncated_summaries += "\n\n[Note: Summaries were truncated due to size]"
            initial_summaries = truncated_summaries
            
        # Simplify findings JSON to reduce size if needed
        findings_str = json.dumps(findings, indent=2)
        if len(findings_str) > 10000:
            logging.warning(f"Findings JSON too large ({len(findings_str)} chars), simplifying")
            # Create a simplified version with just the key information
            simplified_findings = {
                'root_summary': findings.get('root_summary', ''),
                'directories': {},
                'files': {}
            }
            
            # Include only the first 10 and last 10 directories
            dirs = list(findings.get('directories', {}).items())
            if len(dirs) > 20:
                for k, v in dirs[:10] + dirs[-10:]:
                    simplified_findings['directories'][k] = v
            else:
                simplified_findings['directories'] = findings.get('directories', {})
                
            # Include only the first 20 and last 20 files
            files = list(findings.get('files', {}).items())
            if len(files) > 40:
                for k, v in files[:20] + files[-20:]:
                    simplified_findings['files'][k] = v
            else:
                simplified_findings['files'] = findings.get('files', {})
                
            findings_str = json.dumps(simplified_findings, indent=2)
            
        message = self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            temperature=0,
            system="You are an expert technical writer who creates clear, well-organized developer guides.",
            messages=[{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": f"""
Based on the following project analysis data, create a comprehensive developer guide in markdown format.

Initial Summaries:
{initial_summaries}

Detailed Findings:
{findings_str}

Create a developer guide that includes:

1. Executive Summary
   - Project purpose
   - Main technologies
   - Key features

2. Project Architecture
   - High-level overview
   - Key components
   - Design patterns used
   - Data flow

3. Setup & Installation
   - Prerequisites
   - Environment setup
   - Configuration

4. Code Organization
   - Directory structure
   - Key files and their purposes
   - Important modules/packages

5. Core Concepts
   - Main abstractions
   - Key interfaces
   - Data models

6. Development Workflow
   - Building
   - Testing
   - Deployment

7. API Reference
   - Key functions
   - Important classes
   - Public interfaces

8. Common Tasks
   - Example workflows
   - Code examples
   - Best practices

Make the guide practical and easy to follow. Use clear headings, code examples where relevant, and include any important notes or warnings.
"""
                }]
            }]
        )

        return message.content[0].text

def main():
    project_directory = os.environ.get('GUIDE_TARGET_PROJECT_DIRECTORY')
    if not project_directory:
        logging.error("GUIDE_TARGET_PROJECT_DIRECTORY environment variable is not set.")
        return

    # Phase 1: Analyze project
    analyzer = ProjectAnalyzer(project_directory)
    initial_summaries_path, findings_path = analyzer.analyze_project()
    
    # Phase 2: Generate developer guide
    guidebook_path = analyzer.generate_developer_guide()

    logging.info(f"Guide generation complete. Results saved to:\n"
                f"Initial Summaries: {initial_summaries_path}\n"
                f"Findings: {findings_path}\n"
                f"Developer Guide: {guidebook_path}")

if __name__ == "__main__":
    main()


