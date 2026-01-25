"""
Control Flow Graph (CFG) Analysis
Detects identical program logic despite different syntax

Compares the flow of execution rather than code syntax
"""

from typing import Dict, List, Tuple, Set
from dataclasses import dataclass
from collections import defaultdict
import ast


@dataclass
class CFGNode:
    """Node in control flow graph"""
    id: int
    type: str  # 'start', 'end', 'statement', 'condition', 'loop'
    edges: List[int]  # IDs of connected nodes
    
    def __hash__(self):
        return hash(self.id)


class ControlFlowGraph:
    """Represents program control flow"""
    
    def __init__(self):
        self.nodes: Dict[int, CFGNode] = {}
        self.start_node: int = 0
        self.end_node: int = -1
        self.node_counter: int = 0
    
    def add_node(self, node_type: str) -> int:
        """Add new node to graph"""
        node_id = self.node_counter
        self.nodes[node_id] = CFGNode(
            id=node_id,
            type=node_type,
            edges=[]
        )
        self.node_counter += 1
        return node_id
    
    def add_edge(self, from_id: int, to_id: int):
        """Add edge between nodes"""
        if from_id in self.nodes:
            self.nodes[from_id].edges.append(to_id)
    
    def get_structure_signature(self) -> str:
        """Get structural signature of CFG"""
        # DFS traversal to generate signature
        visited = set()
        signature_parts = []
        
        def dfs(node_id: int):
            if node_id in visited or node_id not in self.nodes:
                return
            
            visited.add(node_id)
            node = self.nodes[node_id]
            
            # Add node type to signature
            signature_parts.append(node.type[0].upper())
            
            # Visit children
            for edge in sorted(node.edges):
                dfs(edge)
        
        dfs(self.start_node)
        return ''.join(signature_parts)
    
    def get_complexity_metrics(self) -> Dict:
        """Calculate CFG complexity metrics"""
        total_nodes = len(self.nodes)
        total_edges = sum(len(n.edges) for n in self.nodes.values())
        
        # McCabe's cyclomatic complexity
        # V(G) = E - N + 2P (for single connected graph, P=1)
        cyclomatic = total_edges - total_nodes + 2
        
        # Count decision points (conditions + loops)
        decision_points = sum(
            1 for n in self.nodes.values()
            if n.type in ['condition', 'loop']
        )
        
        return {
            'nodes': total_nodes,
            'edges': total_edges,
            'cyclomatic_complexity': cyclomatic,
            'decision_points': decision_points
        }


class ControlFlowAnalyzer:
    """Analyze and compare control flow graphs"""
    
    def __init__(self):
        self.builder = CFGBuilder()
    
    async def compare(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> Tuple[float, Dict]:
        """
        Compare control flow of two code submissions
        
        Returns:
            (similarity_score, details_dict)
        """
        try:
            # Build CFGs
            cfg1 = self.builder.build(code1, language)
            cfg2 = self.builder.build(code2, language)
            
            # Get structural signatures
            sig1 = cfg1.get_structure_signature()
            sig2 = cfg2.get_structure_signature()
            
            # Compare signatures using edit distance
            similarity = self._compare_signatures(sig1, sig2)
            
            # Get complexity metrics
            metrics1 = cfg1.get_complexity_metrics()
            metrics2 = cfg2.get_complexity_metrics()
            
            # Calculate metrics similarity
            metrics_sim = self._compare_metrics(metrics1, metrics2)
            
            # Combined score (weighted)
            combined_similarity = (similarity * 0.7) + (metrics_sim * 0.3)
            
            details = {
                "signature1": sig1,
                "signature2": sig2,
                "signature_similarity": similarity,
                "metrics1": metrics1,
                "metrics2": metrics2,
                "metrics_similarity": metrics_sim,
                "identical_structure": sig1 == sig2
            }
            
            return combined_similarity, details
            
        except Exception as e:
            return 0.0, {"error": str(e)}
    
    def _compare_signatures(self, sig1: str, sig2: str) -> float:
        """Compare CFG signatures using normalized edit distance"""
        if sig1 == sig2:
            return 1.0
        
        if not sig1 or not sig2:
            return 0.0
        
        # Levenshtein distance
        distance = self._levenshtein_distance(sig1, sig2)
        
        # Normalize by max length
        max_len = max(len(sig1), len(sig2))
        
        return 1 - (distance / max_len) if max_len > 0 else 0.0
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein (edit) distance"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            
            for j, c2 in enumerate(s2):
                # Cost of insertions, deletions, substitutions
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                
                current_row.append(min(insertions, deletions, substitutions))
            
            previous_row = current_row
        
        return previous_row[-1]
    
    def _compare_metrics(self, m1: Dict, m2: Dict) -> float:
        """Compare complexity metrics"""
        # Compare cyclomatic complexity
        cc1 = m1.get('cyclomatic_complexity', 0)
        cc2 = m2.get('cyclomatic_complexity', 0)
        cc_sim = 1 - abs(cc1 - cc2) / max(cc1, cc2, 1)
        
        # Compare decision points
        dp1 = m1.get('decision_points', 0)
        dp2 = m2.get('decision_points', 0)
        dp_sim = 1 - abs(dp1 - dp2) / max(dp1, dp2, 1)
        
        # Compare node counts
        n1 = m1.get('nodes', 0)
        n2 = m2.get('nodes', 0)
        node_sim = min(n1, n2) / max(n1, n2, 1)
        
        # Weighted average
        return (cc_sim * 0.4) + (dp_sim * 0.4) + (node_sim * 0.2)


class CFGBuilder:
    """Build control flow graphs from source code"""
    
    def build(self, code: str, language: str) -> ControlFlowGraph:
        """Build CFG from code"""
        if language == 'python':
            return self._build_python_cfg(code)
        elif language in ['c', 'cpp']:
            return self._build_c_cfg(code)
        else:
            return self._build_generic_cfg(code)
    
    def _build_python_cfg(self, code: str) -> ControlFlowGraph:
        """Build CFG from Python code"""
        import ast
        
        try:
            tree = ast.parse(code)
            cfg = ControlFlowGraph()
            
            # Add start node
            start = cfg.add_node('start')
            cfg.start_node = start
            
            # Process AST
            current = start
            current = self._process_python_ast(tree, cfg, current)
            
            # Add end node
            end = cfg.add_node('end')
            cfg.end_node = end
            cfg.add_edge(current, end)
            
            return cfg
            
        except:
            # Fallback to generic builder
            return self._build_generic_cfg(code)
    
    def _process_python_ast(
        self,
        node: ast.AST,
        cfg: ControlFlowGraph,
        current: int
    ) -> int:
        """Recursively process Python AST nodes"""
        if isinstance(node, ast.Module):
            for stmt in node.body:
                current = self._process_python_ast(stmt, cfg, current)
            return current
        
        elif isinstance(node, ast.If):
            # Create condition node
            cond = cfg.add_node('condition')
            cfg.add_edge(current, cond)
            
            # Process if body
            if_end = cond
            for stmt in node.body:
                if_end = self._process_python_ast(stmt, cfg, if_end)
            
            # Process else body
            else_end = cond
            for stmt in node.orelse:
                else_end = self._process_python_ast(stmt, cfg, else_end)
            
            # Merge point
            merge = cfg.add_node('statement')
            cfg.add_edge(if_end, merge)
            cfg.add_edge(else_end, merge)
            
            return merge
        
        elif isinstance(node, (ast.For, ast.While)):
            # Create loop node
            loop = cfg.add_node('loop')
            cfg.add_edge(current, loop)
            
            # Process loop body
            body_end = loop
            for stmt in node.body:
                body_end = self._process_python_ast(stmt, cfg, body_end)
            
            # Loop back
            cfg.add_edge(body_end, loop)
            
            # Exit point
            exit_node = cfg.add_node('statement')
            cfg.add_edge(loop, exit_node)
            
            return exit_node
        
        else:
            # Regular statement
            stmt = cfg.add_node('statement')
            cfg.add_edge(current, stmt)
            return stmt
    
    def _build_c_cfg(self, code: str) -> ControlFlowGraph:
        """Build CFG from C/C++ code using pattern matching"""
        import re
        
        cfg = ControlFlowGraph()
        
        # Add start
        start = cfg.add_node('start')
        cfg.start_node = start
        current = start
        
        # Remove comments and strings
        code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        code = re.sub(r'"[^"]*"', '""', code)
        
        # Find control structures
        patterns = [
            (r'\bif\s*\(', 'condition'),
            (r'\b(for|while)\s*\(', 'loop'),
            (r'\bswitch\s*\(', 'condition'),
        ]
        
        pos = 0
        for match in re.finditer(r'\b(if|for|while|switch)\s*\(', code):
            # Add statement before control structure
            if match.start() > pos:
                stmt = cfg.add_node('statement')
                cfg.add_edge(current, stmt)
                current = stmt
            
            # Add control structure node
            keyword = match.group(1)
            if keyword in ['for', 'while']:
                node = cfg.add_node('loop')
            else:
                node = cfg.add_node('condition')
            
            cfg.add_edge(current, node)
            current = node
            pos = match.end()
        
        # Add final statement and end
        if pos < len(code):
            stmt = cfg.add_node('statement')
            cfg.add_edge(current, stmt)
            current = stmt
        
        end = cfg.add_node('end')
        cfg.end_node = end
        cfg.add_edge(current, end)
        
        return cfg
    
    def _build_generic_cfg(self, code: str) -> ControlFlowGraph:
        """Generic CFG builder (fallback)"""
        import re
        
        cfg = ControlFlowGraph()
        
        start = cfg.add_node('start')
        cfg.start_node = start
        
        # Count control keywords
        if_count = len(re.findall(r'\bif\b', code, re.IGNORECASE))
        loop_count = len(re.findall(r'\b(for|while)\b', code, re.IGNORECASE))
        
        current = start
        
        # Add condition nodes
        for _ in range(if_count):
            node = cfg.add_node('condition')
            cfg.add_edge(current, node)
            current = node
        
        # Add loop nodes
        for _ in range(loop_count):
            node = cfg.add_node('loop')
            cfg.add_edge(current, node)
            current = node
        
        # Add end
        end = cfg.add_node('end')
        cfg.end_node = end
        cfg.add_edge(current, end)
        
        return cfg
