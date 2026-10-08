// Claude voice assistant: a "Siri replacement" server
// GET  /      -> voice web page (talk to Claude in the browser)
// POST /ask   -> send { "text": "..." }, get back { "reply": "..." }
//                (this is what the iOS Shortcut calls)

let myhttp = require('http');
let myfs = require('fs');
let mypath = require('path');
let Anthropic = require('@anthropic-ai/sdk');

// new Anthropic() reads the ANTHROPIC_API_KEY environment variable
let client = new Anthropic();

let PORT = Number(process.env.PORT) || 8080;
// use HOST=0.0.0.0 so your phone can reach the server over wifi
let HOST = process.env.HOST || "127.0.0.1";
// optional password so strangers on your network can't use your API key
let ASSISTANT_TOKEN = process.env.ASSISTANT_TOKEN || "";

let SYSTEM_PROMPT =
  "You are a voice assistant, like Siri. Your replies are read out loud, " +
  "so answer in one to three short, natural sentences. " +
  "No markdown, lists, code blocks, emoji, or URLs.";

// remember the last few turns so follow-up questions make sense
let history = [];
let MAX_HISTORY = 20;

async function askClaude( text ) {
  history.push( { role: "user", content: text } );

  let response = await client.beta.messages.create({
    model: "claude-opus-5-5",
    max_tokens: 16000,
    // low effort keeps spoken answers fast
    output_config: { effort: "low" },
    // if a request is declined, retry it on Anthropic's recommended fallback model
    betas: [ "server-side-fallback-2026-07-01" ],
    fallbacks: "default",
    system: SYSTEM_PROMPT,
    messages: history
  });

  if ( response.stop_reason === "refusal" ) {
    history.pop();
    return "Sorry, I can't help with that one.";
  }

  let reply = "";
  for ( let block of response.content ) {
    if ( block.type === "text" ) {
      reply += block.text;
    }
  }

  // store only the text so the saved history stays simple
  history.push( { role: "assistant", content: reply } );
  if ( history.length > MAX_HISTORY ) {
    history = history.slice( history.length - MAX_HISTORY );
  }
  return reply;
}

function sendJson( myresponse, status, data ) {
  myresponse.writeHead( status, { "Content-Type": "application/json" } );
  myresponse.end( JSON.stringify( data ) );
}

// read the whole request body (with a size limit)
function readBody( myrequest ) {
  return new Promise( function( resolve, reject ) {
    let body = "";
    myrequest.on( "data", function( chunk ) {
      body += chunk;
      if ( body.length > 100000 ) {
        reject( new Error( "body too large" ) );
        myrequest.destroy();
      }
    });
    myrequest.on( "end", function() { resolve( body ); } );
    myrequest.on( "error", reject );
  });
}

let myserver = myhttp.createServer( async function( myrequest, myresponse ) {
  console.log( myrequest.method, myrequest.url );

  if ( myrequest.method === "GET" && myrequest.url === "/" ) {
    let page = myfs.readFileSync( mypath.join( __dirname, "public", "assistant.html" ) );
    myresponse.writeHead( 200, { "Content-Type": "text/html; charset=utf-8" } );
    myresponse.end( page );
    return;
  }

  if ( myrequest.method === "POST" && ( myrequest.url === "/ask" || myrequest.url === "/reset" ) ) {
    if ( ASSISTANT_TOKEN && myrequest.headers[ "x-assistant-token" ] !== ASSISTANT_TOKEN ) {
      sendJson( myresponse, 401, { error: "wrong or missing X-Assistant-Token header" } );
      return;
    }

    if ( myrequest.url === "/reset" ) {
      history = [];
      sendJson( myresponse, 200, { reply: "Okay, starting fresh." } );
      return;
    }

    try {
      let data = JSON.parse( await readBody( myrequest ) );
      let text = ( data.text || "" ).trim();
      if ( !text ) {
        sendJson( myresponse, 400, { error: "send JSON like { \"text\": \"what time is it in Tokyo\" }" } );
        return;
      }
      let reply = await askClaude( text );
      sendJson( myresponse, 200, { reply: reply } );
    } catch ( err ) {
      console.error( err );
      history = [];
      sendJson( myresponse, 500, { reply: "Sorry, something went wrong talking to Claude." } );
    }
    return;
  }

  myresponse.writeHead( 404, { "Content-Type": "text/plain" } );
  myresponse.end( "not found\n" );
});

myserver.listen( PORT, HOST );
console.log( "Claude assistant running at http://" + HOST + ":" + PORT );
