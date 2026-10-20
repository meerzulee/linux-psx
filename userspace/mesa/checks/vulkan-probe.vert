#version 450
layout(push_constant) uniform Params { vec4 rgba; float depth; } params;
const vec2 positions[3] = vec2[3](vec2(-1, -1), vec2(3, -1), vec2(-1, 3));
void main() { gl_Position = vec4(positions[gl_VertexIndex], params.depth, 1); }
