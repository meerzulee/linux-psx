#version 450
layout(push_constant) uniform Params { vec4 rgba; float depth; } params;
layout(location = 0) out vec4 color;
void main() { color = params.rgba; }
