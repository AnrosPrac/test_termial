"""
Abstract Syntax Tree (AST) Analyzer
Compares program structure independent of variable names and formatting

Supports: Python (native ast), C/C++ (pycparser)
"""

import ast
import hashlib
from typing import Dict, Tuple, Any
from collections import defaultdict


class ASTAnalyzer:
    """Compare code using Abstract Syntax Trees"""
    
    def __init__(self):
        self.python_parser = PythonASTParser()
        self.c_parser = CASTParser()
    
    async def compare(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> Tuple[float, Dict]:
        """
        Compare two code submissions using AST
        
        Returns:
            (similarity_score, details_dict)
        """
        if language == 'python':
            return await self._compare_python(code1, code2)
        elif language in ['c', 'cpp']:
            return await self._compare_c(code1, code2)
        else:
            raise ValueError(f"Unsupported language: {language}")
    
    async def _compare_python(
        self,
        code1: str,
        code2: str
    ) -> Tuple[float, Dict]:
        """Compare Python code using native AST"""
        try:
            tree1 = self.python_parser.parse(code1)
            tree2 = self.python_parser.parse(code2)
            
            # Extract structural features
            features1 = self.python_parser.extract_features(tree1)
            features2 = self.python_parser.extract_features(tree2)
            
            # Calculate similarity
            similarity = self._calculate_feature_similarity(features1, features2)
            
            # Generate details
            details = {
                "tree1_depth": features1['max_depth'],
                "tree2_depth": features2['max_depth'],
                "node_count_diff": abs(features1['node_count'] - features2['node_count']),
                "structural_hash1": features1['structure_hash'],
                "structural_hash2": features2['structure_hash'],
                "common_patterns": self._find_common_patterns(features1, features2)
            }
            
            return similarity, details
            
        except SyntaxError as e:
            return 0.0, {"error": f"Syntax error: {str(e)}"}
        except Exception as e:
            return 0.0, {"error": str(e)}
    
    async def _compare_c(
        self,
        code1: str,
        code2: str
    ) -> Tuple[float, Dict]:
        """Compare C/C++ code using simplified AST"""
        try:
            # Preprocess code (remove comments, normalize)
            clean1 = self.c_parser.preprocess(code1)
            clean2 = self.c_parser.preprocess(code2)
            
            # Extract structural features
            features1 = self.c_parser.extract_features(clean1)
            features2 = self.c_parser.extract_features(clean2)
            
            # Calculate similarity
            similarity = self._calculate_feature_similarity(features1, features2)
            
            details = {
                "function_count1": features1['function_count'],
                "function_count2": features2['function_count'],
                "control_structures1": features1['control_count'],
                "control_structures2": features2['control_count'],
                "common_patterns": self._find_common_patterns(features1, features2)
            }
            
            return similarity, details
            
        except Exception as e:
            return 0.0, {"error": str(e)}
    
    def _calculate_feature_similarity(
        self,
        features1: Dict,
        features2: Dict
    ) -> float:
        """Calculate similarity based on extracted features"""
        # Compare structural hashes
        if features1.get('structure_hash') == features2.get('structure_hash'):
            return 1.0
        
        # Compare node type distributions
        dist1 = features1.get('node_distribution', {})
        dist2 = features2.get('node_distribution', {})
        
        dist_similarity = self._compare_distributions(dist1, dist2)
        
        # Compare depths
        depth1 = features1.get('max_depth', 0)
        depth2 = features2.get('max_depth', 0)
        depth_similarity = 1 - abs(depth1 - depth2) / max(depth1, depth2, 1)
        
        # Compare node counts
        count1 = features1.get('node_count', 0)
        count2 = features2.get('node_count', 0)
        count_similarity = min(count1, count2) / max(count1, count2, 1)
        
        # Weighted average
        return (
            dist_similarity * 0.5 +
            depth_similarity * 0.25 +
            count_similarity * 0.25
        )
    
    def _compare_distributions(
        self,
        dist1: Dict[str, int],
        dist2: Dict[str, int]
    ) -> float:
        """Compare two frequency distributions using cosine similarity"""
        all_keys = set(dist1.keys()) | set(dist2.keys())
        
        if not all_keys:
            return 1.0
        
        dot_product = sum(dist1.get(k, 0) * dist2.get(k, 0) for k in all_keys)
        
        magnitude1 = sum(v**2 for v in dist1.values()) ** 0.5
        magnitude2 = sum(v**2 for v in dist2.values()) ** 0.5
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)
    
    def _find_common_patterns(
        self,
        features1: Dict,
        features2: Dict
    ) -> list:
        """Identify common structural patterns"""
        patterns = []
        
        # Check for identical control flow patterns
        if features1.get('control_pattern') == features2.get('control_pattern'):
            patterns.append("Identical control flow structure")
        
        # Check for similar loop counts
        loops1 = features1.get('loop_count', 0)
        loops2 = features2.get('loop_count', 0)
        if loops1 == loops2 and loops1 > 0:
            patterns.append(f"Same number of loops ({loops1})")
        
        # Check for similar function calls
        calls1 = set(features1.get('function_calls', []))
        calls2 = set(features2.get('function_calls', []))
        common_calls = calls1 & calls2
        if len(common_calls) > 3:
            patterns.append(f"Common function calls: {len(common_calls)}")
        
        return patterns


class PythonASTParser:
    """Parse and analyze Python AST"""
    
    def parse(self, code: str) -> ast.AST:
        """Parse Python code into AST"""
        return ast.parse(code)
    
    def extract_features(self, tree: ast.AST) -> Dict:
        """Extract structural features from AST"""
        visitor = FeatureVisitor()
        visitor.visit(tree)
        
        return {
            'node_count': visitor.node_count,
            'max_depth': visitor.max_depth,
            'node_distribution': visitor.node_types,
            'structure_hash': self._compute_structure_hash(tree),
            'loop_count': visitor.loop_count,
            'function_count': visitor.function_count,
            'control_count': visitor.control_count,
            'function_calls': visitor.function_calls,
            'control_pattern': visitor.control_pattern
        }
    
    def _compute_structure_hash(self, tree: ast.AST) -> str:
        """Compute hash of tree structure (ignoring names/values)"""
        structure = self._tree_to_structure(tree)
        return hashlib.md5(structure.encode()).hexdigest()
    
    def _tree_to_structure(self, node: ast.AST) -> str:
        """Convert AST to structure-only representation"""
        if isinstance(node, ast.AST):
            # Get node type
            parts = [node.__class__.__name__]
            
            # Add child structures
            for field, value in ast.iter_fields(node):
                # Skip name/value fields
                if field in ['id', 'n', 's', 'name', 'arg']:
                    continue
                
                if isinstance(value, list):
                    parts.extend(self._tree_to_structure(item) for item in value)
                elif isinstance(value, ast.AST):
                    parts.append(self._tree_to_structure(value))
            
            return f"({','.join(parts)})"
        return ""


class FeatureVisitor(ast.NodeVisitor):
    """AST visitor to extract features"""
    
    def __init__(self):
        self.node_count = 0
        self.max_depth = 0
        self.current_depth = 0
        self.node_types = defaultdict(int)
        self.loop_count = 0
        self.function_count = 0
        self.control_count = 0
        self.function_calls = []
        self.control_pattern = []
    
    def visit(self, node):
        """Visit node and track features"""
        self.node_count += 1
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        
        node_type = node.__class__.__name__
        self.node_types[node_type] += 1
        
        # Track control structures
        if isinstance(node, (ast.For, ast.While)):
            self.loop_count += 1
            self.control_pattern.append('L')
        elif isinstance(node, (ast.If,)):
            self.control_count += 1
            self.control_pattern.append('I')
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.function_count += 1
            self.control_pattern.append('F')
        elif isinstance(node, ast.Call):
            if hasattr(node.func, 'id'):
                self.function_calls.append(node.func.id)
        
        self.generic_visit(node)
        self.current_depth -= 1


class CASTParser:
    """Simplified C/C++ AST parser"""
    
    def preprocess(self, code: str) -> str:
        """Remove comments and normalize whitespace"""
        import re
        
        # Remove single-line comments
        code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
        
        # Remove multi-line comments
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        
        # Normalize whitespace
        code = re.sub(r'\s+', ' ', code)
        
        return code.strip()
    
    def extract_features(self, code: str) -> Dict:
        """Extract features from C/C++ code using regex patterns"""
        import re
        
        features = {
            'function_count': len(re.findall(r'\b\w+\s+\w+\s*\([^)]*\)\s*\{', code)),
            'loop_count': len(re.findall(r'\b(for|while)\s*\(', code)),
            'control_count': len(re.findall(r'\bif\s*\(', code)),
            'node_count': len(code.split()),
            'max_depth': self._estimate_depth(code),
            'function_calls': re.findall(r'\b(\w+)\s*\(', code),
            'control_pattern': self._extract_control_pattern(code),
            'node_distribution': self._count_keywords(code),
            'structure_hash': hashlib.md5(
                self._normalize_structure(code).encode()
            ).hexdigest()
        }
        
        return features
    
    def _estimate_depth(self, code: str) -> int:
        """Estimate nesting depth from braces"""
        max_depth = 0
        current_depth = 0
        
        for char in code:
            if char == '{':
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif char == '}':
                current_depth = max(0, current_depth - 1)
        
        return max_depth
    
    def _extract_control_pattern(self, code: str) -> list:
        """Extract control structure pattern"""
        import re
        pattern = []
        
        for match in re.finditer(r'\b(for|while|if|switch)\b', code):
            keyword = match.group(1)
            if keyword in ['for', 'while']:
                pattern.append('L')
            elif keyword == 'if':
                pattern.append('I')
            elif keyword == 'switch':
                pattern.append('S')
        
        return pattern
    
    def _count_keywords(self, code: str) -> Dict[str, int]:
        """Count C/C++ keywords"""
        import re
        keywords = [
            'int', 'float', 'char', 'void', 'return',
            'if', 'else', 'for', 'while', 'do',
            'switch', 'case', 'break', 'continue'
        ]
        
        counts = {}
        for keyword in keywords:
            counts[keyword] = len(re.findall(rf'\b{keyword}\b', code))
        
        return counts
    
    def _normalize_structure(self, code: str) -> str:
        """Normalize code to structure-only form"""
        import re
        
        # Replace all identifiers with 'X'
        code = re.sub(r'\b[a-zA-Z_]\w*\b', 'X', code)
        
        # Replace all numbers with 'N'
        code = re.sub(r'\b\d+\b', 'N', code)
        
        # Remove whitespace
        code = re.sub(r'\s+', '', code)
        
        return code
